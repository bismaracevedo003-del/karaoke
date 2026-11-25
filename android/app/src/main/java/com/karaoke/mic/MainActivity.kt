package com.karaoke.mic

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.MediaStore
import android.provider.Settings
import android.text.InputType
import android.view.Gravity
import android.view.ViewGroup
import android.view.WindowInsets
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

class MainActivity : Activity() {
    private lateinit var ipInput: EditText
    private lateinit var portInput: EditText
    private lateinit var connectButton: Button
    private lateinit var levelBar: ProgressBar
    private lateinit var statusText: TextView
    private lateinit var statusDot: android.view.View
    private lateinit var qualityText: TextView

    private val prefs by lazy { getSharedPreferences("karaoke_mic", Context.MODE_PRIVATE) }

    private val bg = Color.parseColor("#0B0716")
    private val cardBg = Color.parseColor("#171126")
    private val cardStroke = Color.parseColor("#2C2347")
    private val fieldBg = Color.parseColor("#0F0B1E")
    private val accent = Color.parseColor("#8B5CF6")
    private val accent2 = Color.parseColor("#EC4899")
    private val textMain = Color.parseColor("#F5F3FF")
    private val textMuted = Color.parseColor("#A79FC0")
    private val textSoft = Color.parseColor("#CFC6E8")
    private val okColor = Color.parseColor("#34D399")
    private val warnColor = Color.parseColor("#FBBF24")
    private val badColor = Color.parseColor("#F87171")

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                MicService.ACTION_LEVEL -> {
                    levelBar.progress = intent.getIntExtra(MicService.EXTRA_LEVEL, 0)
                }

                MicService.ACTION_STATE -> {
                    val running = intent.getBooleanExtra(MicService.EXTRA_RUNNING, false)
                    val message = intent.getStringExtra(MicService.EXTRA_MESSAGE)
                        ?: if (running) "Activo" else "Detenido"
                    setStatus(running, message)
                }

                MicService.ACTION_QUALITY -> {
                    if (!MicService.running) return
                    val quality = intent.getIntExtra(MicService.EXTRA_QUALITY, -1)
                    if (quality >= 0) {
                        qualityText.text = "Calidad: $quality%"
                        qualityText.setTextColor(
                            when {
                                quality >= 90 -> okColor
                                quality >= 70 -> warnColor
                                else -> badColor
                            }
                        )
                    } else {
                        qualityText.text = "Sin respuesta del PC"
                        qualityText.setTextColor(badColor)
                    }
                }
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = bg
        window.navigationBarColor = bg
        setContentView(buildUi())
        handleDeepLink(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleDeepLink(intent)
    }

    override fun onStart() {
        super.onStart()
        val filter = IntentFilter().apply {
            addAction(MicService.ACTION_LEVEL)
            addAction(MicService.ACTION_STATE)
            addAction(MicService.ACTION_QUALITY)
        }
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            @Suppress("UnspecifiedRegisterReceiverFlag")
            registerReceiver(receiver, filter)
        }
        syncState()
    }

    override fun onStop() {
        try {
            unregisterReceiver(receiver)
        } catch (_: Exception) {
        }
        super.onStop()
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun rounded(color: Int, radius: Int, stroke: Int? = null): GradientDrawable =
        GradientDrawable().apply {
            shape = GradientDrawable.RECTANGLE
            cornerRadius = dp(radius).toFloat()
            setColor(color)
            if (stroke != null) setStroke(dp(1), stroke)
        }

    private fun gradient(vararg colors: Int, radius: Int = 16): GradientDrawable =
        GradientDrawable(GradientDrawable.Orientation.TL_BR, colors).apply {
            cornerRadius = dp(radius).toFloat()
        }

    private fun oval(color: Int): GradientDrawable =
        GradientDrawable().apply {
            shape = GradientDrawable.OVAL
            setColor(color)
        }

    private fun label(text: String, size: Float, color: Int, bold: Boolean = false): TextView =
        TextView(this).apply {
            this.text = text
            textSize = size
            setTextColor(color)
            if (bold) setTypeface(typeface, Typeface.BOLD)
        }

    private fun card(top: Int = 16): LinearLayout =
        LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(18), dp(18), dp(18))
            background = rounded(cardBg, 20, cardStroke)
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(top) }
        }

    private fun actionButton(textValue: String, colors: IntArray? = null): Button =
        Button(this).apply {
            text = textValue
            isAllCaps = false
            textSize = 16f
            setTypeface(typeface, Typeface.BOLD)
            setTextColor(Color.WHITE)
            stateListAnimator = null
            setPadding(dp(16), dp(13), dp(16), dp(13))
            background = if (colors != null) {
                gradient(*colors)
            } else {
                rounded(Color.parseColor("#1B1430"), 16, cardStroke)
            }
        }

    private fun inputField(hintValue: String, input: Int, value: String): EditText =
        EditText(this).apply {
            hint = hintValue
            inputType = input
            isSingleLine = true
            textSize = 16f
            setTextColor(textMain)
            setHintTextColor(textMuted)
            setPadding(dp(14), dp(13), dp(14), dp(13))
            background = rounded(fieldBg, 14, cardStroke)
            setText(value)
        }

    private fun buildUi(): ScrollView {
        val scroll = ScrollView(this).apply {
            isFillViewport = true
            setBackgroundColor(bg)
        }
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(24), dp(20), dp(28))
        }
        scroll.addView(
            root,
            ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            )
        )
        scroll.setOnApplyWindowInsetsListener { _, insets ->
            val top: Int
            val bottom: Int
            if (Build.VERSION.SDK_INT >= 30) {
                val bars = insets.getInsets(WindowInsets.Type.systemBars())
                top = bars.top
                bottom = bars.bottom
            } else {
                @Suppress("DEPRECATION")
                top = insets.systemWindowInsetTop
                @Suppress("DEPRECATION")
                bottom = insets.systemWindowInsetBottom
            }
            root.setPadding(dp(20), dp(24) + top, dp(20), dp(28) + bottom)
            insets
        }

        val header = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        val logo = ImageView(this).apply { setImageResource(R.mipmap.ic_launcher) }
        header.addView(logo, LinearLayout.LayoutParams(dp(58), dp(58)))
        val titles = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        titles.addView(label(getString(R.string.app_name), 23f, textMain, true))
        titles.addView(label("Micrófono por Wi‑Fi", 13f, textMuted).apply { setPadding(0, dp(2), 0, 0) })
        header.addView(
            titles,
            LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                marginStart = dp(14)
            }
        )
        root.addView(header)

        val statusCard = card(20)
        root.addView(statusCard)
        val statusRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        statusDot = android.view.View(this).apply { background = oval(textMuted) }
        statusRow.addView(
            statusDot,
            LinearLayout.LayoutParams(dp(10), dp(10)).apply { marginEnd = dp(10) }
        )
        statusText = label("Detenido", 16f, textMain, true)
        statusRow.addView(statusText, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        qualityText = label("Calidad: —", 12f, textMuted)
        statusRow.addView(qualityText)
        statusCard.addView(statusRow)

        levelBar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            progressTintList = ColorStateList.valueOf(Color.parseColor("#22D3EE"))
            progressBackgroundTintList = ColorStateList.valueOf(Color.parseColor("#2A2140"))
        }
        statusCard.addView(
            levelBar,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(8)).apply {
                topMargin = dp(14)
            }
        )
        statusCard.addView(
            label("Sigue activo con la pantalla apagada.", 12f, textMuted).apply {
                setPadding(0, dp(10), 0, 0)
            }
        )

        val connCard = card(16)
        root.addView(connCard)
        connCard.addView(label("Conexión con la PC", 13f, textMuted, true))

        val fields = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        ipInput = inputField(
            "IP de la PC",
            InputType.TYPE_CLASS_TEXT,
            prefs.getString("ip", "").orEmpty()
        )
        fields.addView(ipInput, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        portInput = inputField(
            "Puerto",
            InputType.TYPE_CLASS_NUMBER,
            prefs.getInt("port", 50007).toString()
        )
        fields.addView(
            portInput,
            LinearLayout.LayoutParams(dp(96), ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                marginStart = dp(8)
            }
        )
        connCard.addView(
            fields,
            LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT
            ).apply { topMargin = dp(10) }
        )

        connectButton = actionButton("Conectar", intArrayOf(accent, accent2)).apply {
            setOnClickListener { toggleSend() }
        }
        connCard.addView(
            connectButton,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(52)).apply {
                topMargin = dp(12)
            }
        )

        val qrButton = actionButton("Escanear QR de la PC").apply {
            setTextColor(textMain)
            setOnClickListener { openCameraForQr() }
        }
        connCard.addView(
            qrButton,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(48)).apply {
                topMargin = dp(10)
            }
        )
        connCard.addView(
            label("Escaneá el QR del PC: la app se abre y se conecta sola.", 12f, textMuted).apply {
                gravity = Gravity.CENTER
                setPadding(0, dp(10), 0, 0)
            }
        )

        val tipsCard = card(16)
        root.addView(tipsCard)
        tipsCard.addView(label("Para que no se corte", 13f, textMuted, true))
        for (tip in listOf(
            "• PC y celular en la misma Wi‑Fi (mejor 5 GHz).",
            "• La pantalla puede estar apagada.",
            "• Evitá el ahorro de batería agresivo."
        )) {
            tipsCard.addView(
                label(tip, 13f, textSoft).apply { setPadding(0, dp(6), 0, 0) }
            )
        }
        val batteryButton = actionButton("Quitar restricción de batería").apply {
            setTextColor(textMain)
            setOnClickListener { requestBatteryExemption() }
        }
        tipsCard.addView(
            batteryButton,
            LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(46)).apply {
                topMargin = dp(12)
            }
        )

        root.addView(
            label("Karaoke PC + Karaoke Móvil", 11f, Color.parseColor("#6F6690")).apply {
                gravity = Gravity.CENTER
                setPadding(0, dp(20), 0, 0)
            }
        )

        scroll.requestApplyInsets()
        return scroll
    }

    private fun handleDeepLink(intent: Intent?) {
        if (intent?.action != Intent.ACTION_VIEW) return
        val data = intent.data ?: return
        if (!"karaoke".equals(data.scheme, ignoreCase = true)) return
        val ip = data.getQueryParameter("ip")?.trim().orEmpty()
        val port = data.getQueryParameter("port")?.toIntOrNull()
        if (ip.isEmpty() || port == null || port !in 1..65535) {
            setStatus(false, "Enlace del QR inválido")
            return
        }
        ipInput.setText(ip)
        portInput.setText(port.toString())
        prefs.edit().putString("ip", ip).putInt("port", port).apply()
        if (MicService.running) {
            syncState()
            return
        }
        setStatus(false, "QR recibido: conectando…")
        requestPermissionAndStart()
    }

    private fun toggleSend() {
        if (MicService.running) {
            startService(Intent(this, MicService::class.java).setAction(MicService.ACTION_STOP))
            setStatus(false, "Deteniendo…")
        } else {
            requestPermissionAndStart()
        }
    }

    private fun requestPermissionAndStart() {
        if (Build.VERSION.SDK_INT < 23) {
            startFromUi()
            return
        }
        val needed = mutableListOf<String>()
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            needed += Manifest.permission.RECORD_AUDIO
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            needed += Manifest.permission.POST_NOTIFICATIONS
        }
        if (needed.isEmpty()) {
            startFromUi()
        } else {
            requestPermissions(needed.toTypedArray(), 100)
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != 100) return
        if (Build.VERSION.SDK_INT >= 23 &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED
        ) {
            setStatus(false, "Permiso de micrófono denegado")
            return
        }
        startFromUi()
    }

    private fun startFromUi() {
        if (MicService.running) {
            syncState()
            return
        }
        val ip = ipInput.text.toString().trim()
        val port = portInput.text.toString().trim().toIntOrNull()
        if (ip.isEmpty() || port == null || port !in 1..65535) {
            setStatus(false, "Revisá la IP y el puerto")
            return
        }
        prefs.edit().putString("ip", ip).putInt("port", port).apply()
        val intent = Intent(this, MicService::class.java).apply {
            action = MicService.ACTION_START
            putExtra(MicService.EXTRA_IP, ip)
            putExtra(MicService.EXTRA_PORT, port)
        }
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
        qualityText.text = "Calidad: —"
        qualityText.setTextColor(textMuted)
        setStatus(true, "Iniciando…")
    }

    private fun openCameraForQr() {
        try {
            startActivity(Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA))
            Toast.makeText(this, "Apuntá al QR que muestra la PC", Toast.LENGTH_LONG).show()
        } catch (_: Exception) {
            AlertDialog.Builder(this)
                .setTitle("Escanear QR")
                .setMessage("Abrí la cámara del celular y apuntá al QR de la PC. También podés tocar «Abrir página» en la PC.")
                .setPositiveButton("Entendido", null)
                .show()
        }
    }

    private fun requestBatteryExemption() {
        try {
            startActivity(
                Intent(
                    Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                    Uri.parse("package:$packageName")
                )
            )
        } catch (_: Exception) {
            try {
                startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
            } catch (_: Exception) {
                Toast.makeText(this, "Abrí Ajustes → Batería → Sin restricciones", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun syncState() {
        if (MicService.running) {
            setStatus(true, "Activo: podés apagar la pantalla")
        } else {
            setStatus(false, "Detenido")
        }
    }

    private fun setStatus(running: Boolean, message: String) {
        val isError = message.startsWith("Error")
        val isReconnect = message.contains("Reconectando") || message.contains("Iniciando")
        statusText.text = message
        statusText.setTextColor(
            when {
                isError -> badColor
                isReconnect -> warnColor
                else -> textMain
            }
        )
        statusDot.background = oval(
            when {
                isError -> badColor
                isReconnect -> warnColor
                running -> okColor
                else -> textMuted
            }
        )
        connectButton.text = if (running) "Detener" else "Conectar"
        if (!running) {
            levelBar.progress = 0
            qualityText.text = "Calidad: —"
            qualityText.setTextColor(textMuted)
        }
    }
}
