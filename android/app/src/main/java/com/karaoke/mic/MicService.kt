package com.karaoke.mic

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.Process
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.concurrent.thread
import kotlin.math.roundToInt
import kotlin.math.sqrt

class MicService : Service() {
    companion object {
        const val ACTION_START = "com.karaoke.mic.action.START"
        const val ACTION_STOP = "com.karaoke.mic.action.STOP"
        const val EXTRA_IP = "ip"
        const val EXTRA_PORT = "port"
        const val ACTION_LEVEL = "com.karaoke.mic.action.LEVEL"
        const val ACTION_STATE = "com.karaoke.mic.action.STATE"
        const val ACTION_QUALITY = "com.karaoke.mic.action.QUALITY"
        const val EXTRA_LEVEL = "level"
        const val EXTRA_RUNNING = "running"
        const val EXTRA_MESSAGE = "message"
        const val EXTRA_QUALITY = "quality"
        private const val CHANNEL_ID = "karaoke_mic"
        private const val NOTIFICATION_ID = 41
        private val ACK_MAGIC = "KRM2".toByteArray(Charsets.US_ASCII)

        @Volatile
        var running = false
            private set
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private var recordThread: Thread? = null
    private var ackThread: Thread? = null
    private var sendSocket: DatagramSocket? = null

    @Volatile
    private var active = false

    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    private val immutableFlag: Int
        get() = if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        if (Build.VERSION.SDK_INT >= 26) {
            val manager = getSystemService(NotificationManager::class.java)
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Micrófono activo",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Mantiene el micrófono enviando audio con la pantalla apagada"
                setShowBadge(false)
            }
            manager.createNotificationChannel(channel)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                val ip = intent.getStringExtra(EXTRA_IP).orEmpty().trim()
                val port = intent.getIntExtra(EXTRA_PORT, 0)
                if (ip.isEmpty() || port !in 1..65535) {
                    broadcastState(false, "Dirección inválida")
                    stopSelf()
                    return START_NOT_STICKY
                }
                startForegroundNow("Conectando con $ip:$port…")
                startStream(ip, port)
            }

            ACTION_STOP -> {
                stopStream()
                stopSelf()
            }
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        active = false
        running = false
        closeSocket()
        joinThread(recordThread, 1500)
        joinThread(ackThread, 700)
        recordThread = null
        ackThread = null
        releaseLocks()
        super.onDestroy()
    }

    private fun startStream(ip: String, port: Int) {
        if (active) return
        val created = createRecorder()
        if (created == null) {
            broadcastState(false, "No se pudo abrir el micrófono")
            stopSelf()
            return
        }
        val (recorder, rate) = created
        val sock = try {
            DatagramSocket()
        } catch (e: Exception) {
            recorder.release()
            broadcastState(false, "Error de red: ${e.message ?: "desconocido"}")
            stopSelf()
            return
        }
        try {
            sock.sendBufferSize = 256 * 1024
        } catch (_: Exception) {
        }
        sendSocket = sock
        active = true
        running = true
        acquireLocks()

        val frameSamples = rate / 50
        val buffer = ByteArray(frameSamples * 2)
        val magic = "KRM1".toByteArray(Charsets.US_ASCII)
        startAckListener(sock)
        broadcastState(true, "Conectando con $ip:$port…")

        recordThread = thread(name = "mic-stream") {
            var failure: String? = null
            try {
                Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
                var address: InetAddress? = null
                var reconnecting = false
                recorder.startRecording()
                updateNotification("Enviando a $ip:$port")
                broadcastState(true, "Enviando a $ip:$port")

                var seq = 0
                var uiFrames = 0
                while (active) {
                    var read = 0
                    while (read < buffer.size && active) {
                        val count = recorder.read(buffer, read, buffer.size - read)
                        if (count <= 0) break
                        read += count
                    }
                    if (read < buffer.size) break

                    var energy = 0.0
                    var i = 0
                    while (i + 1 < read) {
                        val raw = (buffer[i].toInt() and 0xFF) or ((buffer[i + 1].toInt() and 0xFF) shl 8)
                        val signed = if (raw > 32767) raw - 65536 else raw
                        energy += signed * signed.toDouble()
                        i += 2
                    }
                    uiFrames++
                    if (uiFrames >= 5) {
                        val level = sqrt(energy / frameSamples) / 32768.0
                        sendBroadcast(
                            Intent(ACTION_LEVEL)
                                .setPackage(packageName)
                                .putExtra(EXTRA_LEVEL, (level * 250).toInt().coerceIn(0, 100))
                        )
                        uiFrames = 0
                    }

                    val packet = ByteBuffer.allocate(12 + read).order(ByteOrder.LITTLE_ENDIAN)
                    packet.put(magic)
                    packet.putInt(seq++)
                    packet.putInt(rate)
                    packet.put(buffer)
                    val payload = packet.array()

                    if (address == null) {
                        try {
                            address = InetAddress.getByName(ip)
                        } catch (_: Exception) {
                            if (!reconnecting) {
                                reconnecting = true
                                broadcastState(true, "Reconectando…")
                            }
                        }
                    }
                    if (address == null) {
                        if (!sleepWhileActive(700)) break
                        continue
                    }

                    try {
                        sock.send(DatagramPacket(payload, payload.size, address, port))
                        sock.send(DatagramPacket(payload, payload.size, address, port))
                        if (reconnecting) {
                            reconnecting = false
                            updateNotification("Enviando a $ip:$port")
                            broadcastState(true, "Enviando a $ip:$port")
                        }
                    } catch (_: Exception) {
                        if (!active) break
                        address = null
                        if (!reconnecting) {
                            reconnecting = true
                            broadcastState(true, "Reconectando…")
                        }
                        if (!sleepWhileActive(800)) break
                    }
                }
            } catch (e: Exception) {
                failure = e.message ?: "error desconocido"
            } finally {
                try {
                    recorder.stop()
                } catch (_: Exception) {
                }
                recorder.release()
                closeSocket()
                active = false
                running = false
                releaseLocks()
                broadcastState(false, if (failure == null) "Detenido" else "Error: $failure")
                mainHandler.post { stopSelf() }
            }
        }
    }

    private fun startAckListener(sock: DatagramSocket) {
        ackThread = thread(name = "mic-ack") {
            try {
                sock.soTimeout = 1200
            } catch (_: Exception) {
            }
            val buf = ByteArray(64)
            var misses = 0
            while (active) {
                try {
                    val datagram = DatagramPacket(buf, buf.size)
                    sock.receive(datagram)
                    if (datagram.length < 12) continue
                    val bb = ByteBuffer.wrap(buf, 0, datagram.length).order(ByteOrder.LITTLE_ENDIAN)
                    val magic = ByteArray(4)
                    bb.get(magic)
                    if (!magic.contentEquals(ACK_MAGIC)) continue
                    val received = bb.int
                    val lost = bb.int
                    misses = 0
                    val total = received.toLong() + lost.toLong()
                    val quality = if (total <= 0L) {
                        100
                    } else {
                        ((received * 100.0) / total).roundToInt().coerceIn(0, 100)
                    }
                    sendBroadcast(
                        Intent(ACTION_QUALITY)
                            .setPackage(packageName)
                            .putExtra(EXTRA_QUALITY, quality)
                    )
                } catch (_: SocketTimeoutException) {
                    if (!active) break
                    misses++
                    if (misses >= 3) {
                        misses = 0
                        sendBroadcast(
                            Intent(ACTION_QUALITY)
                                .setPackage(packageName)
                                .putExtra(EXTRA_QUALITY, -1)
                        )
                    }
                } catch (_: Exception) {
                    if (!active) break
                    try {
                        Thread.sleep(300)
                    } catch (_: InterruptedException) {
                        break
                    }
                }
            }
        }
    }

    private fun sleepWhileActive(ms: Long): Boolean {
        val until = System.currentTimeMillis() + ms
        while (active && System.currentTimeMillis() < until) {
            try {
                Thread.sleep(50)
            } catch (_: InterruptedException) {
                return active
            }
        }
        return active
    }

    private fun stopStream() {
        active = false
        closeSocket()
        joinThread(recordThread, 1500)
        joinThread(ackThread, 700)
        recordThread = null
        ackThread = null
    }

    private fun joinThread(target: Thread?, timeoutMs: Long) {
        try {
            target?.join(timeoutMs)
        } catch (_: Exception) {
        }
    }

    private fun closeSocket() {
        try {
            sendSocket?.close()
        } catch (_: Exception) {
        }
        sendSocket = null
    }

    private fun createRecorder(): Pair<AudioRecord, Int>? {
        for (rate in intArrayOf(48000, 44100, 22050)) {
            val min = AudioRecord.getMinBufferSize(
                rate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT
            )
            if (min <= 0) continue
            val recorder = AudioRecord(
                MediaRecorder.AudioSource.MIC,
                rate,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                min * 4
            )
            if (recorder.state == AudioRecord.STATE_INITIALIZED) return recorder to rate
            recorder.release()
        }
        return null
    }

    private fun acquireLocks() {
        if (wakeLock == null) {
            try {
                val power = getSystemService(Context.POWER_SERVICE) as PowerManager
                wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "KaraokeMic::cpu").apply {
                    setReferenceCounted(false)
                    acquire()
                }
            } catch (_: Exception) {
            }
        }
        if (wifiLock == null) {
            try {
                val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
                val mode = if (Build.VERSION.SDK_INT >= 29) {
                    WifiManager.WIFI_MODE_FULL_LOW_LATENCY
                } else {
                    @Suppress("DEPRECATION")
                    WifiManager.WIFI_MODE_FULL_HIGH_PERF
                }
                wifiLock = wifi.createWifiLock(mode, "KaraokeMic::wifi").apply {
                    setReferenceCounted(false)
                    acquire()
                }
            } catch (_: Exception) {
            }
        }
    }

    private fun releaseLocks() {
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (_: Exception) {
        }
        wakeLock = null
        try {
            if (wifiLock?.isHeld == true) wifiLock?.release()
        } catch (_: Exception) {
        }
        wifiLock = null
    }

    private fun startForegroundNow(message: String) {
        val notification = buildNotification(message)
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun updateNotification(message: String) {
        try {
            val manager = getSystemService(NotificationManager::class.java)
            manager.notify(NOTIFICATION_ID, buildNotification(message))
        } catch (_: Exception) {
        }
    }

    private fun buildNotification(message: String): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java).addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            ),
            PendingIntent.FLAG_UPDATE_CURRENT or immutableFlag
        )
        val builder = if (Build.VERSION.SDK_INT >= 26) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            Notification.Builder(this)
        }
        builder.setContentTitle(getString(R.string.app_name))
            .setContentText(message)
            .setSmallIcon(R.drawable.ic_stat_mic)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setCategory(Notification.CATEGORY_SERVICE)
            .setContentIntent(openIntent)
        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, MicService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or immutableFlag
        )
        builder.addAction(R.drawable.ic_stat_mic, "Detener", stopIntent)
        return builder.build()
    }

    private fun broadcastState(isRunning: Boolean, message: String) {
        sendBroadcast(
            Intent(ACTION_STATE)
                .setPackage(packageName)
                .putExtra(EXTRA_RUNNING, isRunning)
                .putExtra(EXTRA_MESSAGE, message)
        )
        if (isRunning) updateNotification(message)
    }
}
