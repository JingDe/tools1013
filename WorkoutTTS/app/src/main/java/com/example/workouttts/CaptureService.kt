package com.example.workouttts

import android.app.Notification
import android.app.NotificationChannel
import android.app.PendingIntent
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.Rect
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.util.DisplayMetrics
import android.widget.Toast
import android.hardware.display.DisplayManager
import androidx.core.app.NotificationCompat
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.TextRecognizer
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * 前台服务：拿到录屏权限后，每 ~0.8s 截取全屏 → 裁剪出 ROI → ML Kit 中文识别 →
 * 与上一帧比对，发现动作名变了就用系统 TTS 念出来（离线中文）。
 */
class CaptureService : Service() {

    companion object {
        var isRunning = false
        const val ACTION_TEST = "TEST_OCR"            // MainActivity 发来：只识别一次、不朗读
        const val ACTION_SAVE_FRAME = "SAVE_DEBUG_FRAME" // 通知栏“存调试帧”按钮发来：抓当前前台画面存盘
        const val ACTION_RESULT = "com.example.workouttts.OCR_RESULT" // 广播识别结果给 MainActivity
        const val ACTION_ERROR = "com.example.workouttts.OCR_ERROR"   // 广播错误信息给 MainActivity
        const val ACTION_INFO = "com.example.workouttts.OCR_INFO"     // 广播普通提示给 MainActivity
        const val ACTION_REGION_SET = "com.example.workouttts.REGION_SET" // 区域已选，通知界面刷新
    }

    private lateinit var projectionManager: MediaProjectionManager
    private var mediaProjection: android.media.projection.MediaProjection? = null
    private var virtualDisplay: android.hardware.display.VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private lateinit var textRecognizer: TextRecognizer
    private lateinit var tts: TextToSpeech
    private var scheduler: ScheduledExecutorService? = null
    private val mainHandler = Handler(Looper.getMainLooper())

    private var region: Rect? = null
    private var prevText = ""
    private var lastSpoken = ""
    private var busy = false   // 防止上一帧 OCR 没返回就又取新帧
    private var screenW = 0    // 录屏分辨率，用于智能模式判断“屏幕上半部”
    private var screenH = 0
    @Volatile private var saveNextFrame = false // 通知栏“存调试帧”置位，下一帧抓当前前台画面存盘

    /**
     * MediaProjection 状态回调。Android 14 (API 34) 强制要求：必须在开始采集
     * （createVirtualDisplay）之前注册，否则抛 IllegalStateException 直接崩溃。
     * 同时用于感知“用户从系统投屏通知里停止了共享”的情况，及时停掉服务。
     */
    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() {
            // 投屏会话被终止（用户停止/系统收回），主动结束服务，避免空转
            mainHandler.post { stopSelf() }
        }
    }

    override fun onCreate() {
        super.onCreate()
        projectionManager = getSystemService(MediaProjectionManager::class.java)
        // 中文识别器（bundled 模型，离线可用）
        textRecognizer = TextRecognition.getClient(ChineseTextRecognizerOptions.Builder().build())
        tts = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                // 设为中文（简体）；若设备缺中文语音数据则退而求其次用中文默认区域
                val r = tts.setLanguage(Locale.SIMPLIFIED_CHINESE)
                if (r == TextToSpeech.LANG_MISSING_DATA || r == TextToSpeech.LANG_NOT_SUPPORTED) {
                    tts.setLanguage(Locale.CHINESE)
                }
            }
        }
        isRunning = true
        startForeground(1, createNotification())
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        intent ?: return START_STICKY

        @Suppress("DEPRECATION")
        val regionExtra: Rect? = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra("region", Rect::class.java)
        } else {
            intent.getParcelableExtra("region")
        }
        if (regionExtra != null) region = regionExtra
        else region = loadRegionFromPrefs()

        @Suppress("DEPRECATION")
        val data: Intent? = if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra("data", Intent::class.java)
        } else {
            intent.getParcelableExtra("data")
        }
        if (intent.hasExtra("resultCode") && data != null) {
            val resultCode = intent.getIntExtra("resultCode", 0)
            setupProjection(resultCode, data)
        }

        if (ACTION_TEST == intent.action) {
            mainHandler.postDelayed({ poll(false) }, 200)
        }
        if (ACTION_SAVE_FRAME == intent.action) {
            // 点通知按钮时通知面板还展开着，立刻抓会截到状态栏。
            // 延迟约 1.5s 让面板收起、用户回到 B站 等前台画面后，再由 poll 周期抓当前帧存盘。
            Toast.makeText(
                this, "2 秒内抓取当前画面，请保持 B站 播放不动", Toast.LENGTH_LONG
            ).show()
            mainHandler.postDelayed({ saveNextFrame = true }, 1500)
        }
        return START_STICKY
    }

    /** 从本地偏好读取已保存的选框（悬浮窗选区域时写入），作为 Intent 传参的兜底。 */
    private fun loadRegionFromPrefs(): Rect? {
        val p = getSharedPreferences("region_prefs", MODE_PRIVATE)
        if (!p.contains("left")) return null
        return Rect(p.getInt("left", 0), p.getInt("top", 0), p.getInt("right", 0), p.getInt("bottom", 0))
    }

    private fun setupProjection(resultCode: Int, data: Intent) {
        mediaProjection = projectionManager.getMediaProjection(resultCode, data)
        // Android 14 起必须先注册回调再开始采集，否则抛 IllegalStateException
        mediaProjection?.registerCallback(projectionCallback, mainHandler)
        val metrics: DisplayMetrics = resources.displayMetrics
        val width = metrics.widthPixels
        val height = metrics.heightPixels
        screenW = width
        screenH = height
        imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        virtualDisplay = mediaProjection?.createVirtualDisplay(
            "WorkoutTTS", width, height, metrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader!!.surface, null, null
        )
        scheduler = Executors.newSingleThreadScheduledExecutor()
        // 启动后 0.5s 开始，之后每 0.8s 取一帧
        scheduler?.scheduleWithFixedDelay({ poll(true) }, 500, 800, TimeUnit.MILLISECONDS)
    }

    /** 把错误信息广播给 MainActivity，让它直接显示在界面上（无电脑也能看到原因）。 */
    private fun reportError(msg: String) {
        try {
            sendBroadcast(Intent(ACTION_ERROR).putExtra("msg", msg))
        } catch (_: Exception) {
        }
    }

    /** 把普通提示广播给 MainActivity（比如调试帧保存成功）。 */
    private fun reportInfo(msg: String) {
        try {
            sendBroadcast(Intent(ACTION_INFO).putExtra("msg", msg))
        } catch (_: Exception) {
        }
    }

    /**
     * 调试用：把当前帧（全屏 + 裁剪区域）存成 PNG 到相册 Pictures/WorkoutTTS，
     * 让你能直接看到 OCR 实际“看到”的画面，用来区分“没抓到”还是“抓到但认不出”。
     */
    private fun saveDebugFrames(full: android.graphics.Bitmap?, cropped: android.graphics.Bitmap?) {
        try {
            val ts = System.currentTimeMillis()
            full?.let { saveOneBitmap(it, "frame_full_$ts.png") }
            cropped?.let { saveOneBitmap(it, "frame_crop_$ts.png") }
            reportInfo("已存调试帧到 相册/WorkoutTTS (full+crop)")
            Toast.makeText(this, "已存调试帧 → 相册/WorkoutTTS", Toast.LENGTH_LONG).show()
        } catch (e: Exception) {
            reportError("存帧失败: ${e.javaClass.simpleName}: ${e.message}")
            Toast.makeText(this, "存帧失败: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    @Suppress("DEPRECATION")
    private fun saveOneBitmap(bitmap: android.graphics.Bitmap, name: String) {
        val resolver = contentResolver
        val values = android.content.ContentValues().apply {
            put(android.provider.MediaStore.Images.Media.DISPLAY_NAME, name)
            put(android.provider.MediaStore.Images.Media.MIME_TYPE, "image/png")
            put(android.provider.MediaStore.Images.Media.RELATIVE_PATH, "Pictures/WorkoutTTS")
        }
        val uri = resolver.insert(android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
            ?: throw RuntimeException("insert uri 失败")
        val out = resolver.openOutputStream(uri)
            ?: throw RuntimeException("openOutputStream 失败")
        out.use { bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, it) }
    }

    /** 取一帧 → 裁剪 ROI → OCR。speak=false 时只识别不朗读（用于测试）。 */
    private fun poll(speak: Boolean) {
        if (busy) return
        val reader = imageReader ?: return
        val image = reader.acquireLatestImage() ?: return
        busy = true
        var full: android.graphics.Bitmap? = null
        var cropped: android.graphics.Bitmap? = null
        try {
            full = imageToBitmap(image)
            val roi = region
            // 智能模式（未选区域）：直接整屏识别，自动挑动作名，不受屏幕方向/全屏限制
            if (roi == null || roi.width() <= 0 || roi.height() <= 0) {
                val screenBmp = full
                if (saveNextFrame) {
                    saveDebugFrames(screenBmp, null)
                    saveNextFrame = false
                }
                val input = InputImage.fromBitmap(screenBmp, 0)
                textRecognizer.process(input)
                    .addOnSuccessListener { vt ->
                        try {
                            val action = pickActionName(vt)
                            if (action.isNotEmpty()) handleResult(action, speak)
                        } catch (e: Exception) {
                            reportError("处理识别结果异常: ${e.javaClass.simpleName}: ${e.message}")
                        }
                        screenBmp.recycle()
                        busy = false
                    }
                    .addOnFailureListener { e ->
                        reportError("OCR失败: ${e.javaClass.simpleName}: ${e.message}")
                        screenBmp.recycle()
                        busy = false
                    }
                full = null
                return
            }
            // 手动框选模式：裁剪 ROI（独立拷贝）
            cropped = try {
                android.graphics.Bitmap.createBitmap(full, roi.left, roi.top, roi.width(), roi.height())
            } catch (e: Exception) {
                reportError("裁剪失败: ${e.javaClass.simpleName}: ${e.message}")
                null
            }
            if (cropped == null) {
                busy = false
                return
            }
            // 测试模式（④ 测试）或“存调试帧”通知按钮触发时，把实际喂给 OCR 的画面存到相册，
            // 便于排查“没抓到/认不出”。后者用于停留在 B站等前台画面时、不切回本 App 也能抓取。
            if (saveNextFrame) {
                saveDebugFrames(full, cropped)
                saveNextFrame = false
            }
            // full 仅是裁剪的源；裁剪出了独立拷贝，立刻回收原始大图降低内存压力。
            if (cropped != full) {
                full.recycle()
            }
            full = null
            // 框内文字往往较小（竖屏小窗里的视频动作名），放大 2 倍再喂给 OCR，
            // 显著提升 ML Kit 对小字号中文的检出率。
            val ocrBmp = if (cropped.width in 1..999) {
                Bitmap.createScaledBitmap(cropped, cropped.width * 2, cropped.height * 2, true)
            } else {
                cropped
            }
            val input = InputImage.fromBitmap(ocrBmp, 0)
            textRecognizer.process(input)
                .addOnSuccessListener { visionText ->
                    try {
                        handleResult(normalize(visionText.text), speak)
                    } catch (e: Exception) {
                        reportError("处理识别结果异常: ${e.javaClass.simpleName}: ${e.message}")
                    }
                    cropped?.recycle()
                    if (ocrBmp != cropped) ocrBmp.recycle()
                    busy = false
                }
                .addOnFailureListener { e ->
                    reportError("OCR失败: ${e.javaClass.simpleName}: ${e.message}")
                    cropped?.recycle()
                    if (ocrBmp != cropped) ocrBmp.recycle()
                    busy = false
                }
        } catch (e: Exception) {
            reportError("取帧/识别异常: ${e.javaClass.simpleName}: ${e.message}")
            busy = false
        } finally {
            full?.recycle()
            image.close()
        }
    }

    /** 变化检测：连续两帧相同、且与上次念过的不一样，才朗读 + 广播。 */
    private fun handleResult(text: String, speak: Boolean) {
        if (text.isEmpty()) {
            prevText = ""
            return
        }
        if (text == prevText && text != lastSpoken) {
            if (speak) {
                tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, null)
                lastSpoken = text
            }
            sendBroadcast(Intent(ACTION_RESULT).putExtra("text", text))
        }
        prevText = text
    }

    private fun normalize(s: String): String {
        return s.lowercase().split("\\s+".toRegex())
            .filter { it.isNotBlank() }.joinToString(" ")
    }

    /**
     * 智能模式：从整屏 OCR 结果里自动挑出“当前动作名”。
     * 启发式：在屏幕上半部、含中文、且字号（包围盒高度）最大的文字块（通常是动作名）；
     * 排除 NEXT/下一个 这类“下一个动作”预告。无需用户画框，也不依赖屏幕方向/全屏。
     */
    private fun pickActionName(vt: com.google.mlkit.vision.text.Text): String {
        var bestText = ""
        var bestH = 0
        for (block in vt.textBlocks) {
            for (line in block.lines) {
                val txt = line.text.trim()
                if (txt.isEmpty()) continue
                // 必须含中文（动作名是中文；纯数字倒计时、英文 UI 都不认）
                if (!txt.any { it in '\u4e00'..'\u9fff' }) continue
                // 排除“下一个动作”预告
                if (txt.contains("NEXT", ignoreCase = true)
                    || txt.contains("下一个") || txt.contains("即将") || txt.contains("下一项")
                ) continue
                val box = line.boundingBox ?: continue
                // 只取屏幕上半部（动作名通常在画面上方，控件/弹幕在下半部）
                if (screenH > 0 && box.centerY() > screenH * 0.6) continue
                val h = box.height()
                if (h > bestH) {
                    bestH = h
                    bestText = txt
                }
            }
        }
        return bestText
    }

    /** 把 ImageReader 的 RGBA_8888 帧转成 Bitmap。 */
    private fun imageToBitmap(image: android.media.Image): Bitmap {
        val plane = image.planes[0]
        val buffer = plane.buffer
        val pixelStride = plane.pixelStride
        val rowStride = plane.rowStride
        val rowPadding = rowStride - pixelStride * image.width
        val bitmap = Bitmap.createBitmap(
            image.width + rowPadding / pixelStride, image.height, Bitmap.Config.ARGB_8888
        )
        bitmap.copyPixelsFromBuffer(buffer)
        return bitmap
    }

    private fun createNotification(): Notification {
        val channelId = "workout_tts_channel"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                channelId, getString(R.string.channel_name), NotificationManager.IMPORTANCE_LOW
            )
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
        // 通知栏“存调试帧”按钮：停留在 B站等前台画面时，点它即可把当前屏幕存到相册，不用切回 App
        val saveIntent = Intent(this, CaptureService::class.java).apply { action = ACTION_SAVE_FRAME }
        val savePi = PendingIntent.getService(
            this, 2, saveIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, channelId)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.notification_text))
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .addAction(android.R.drawable.ic_menu_camera, "存调试帧", savePi)
            .build()
    }

    override fun onDestroy() {
        super.onDestroy()
        scheduler?.shutdown()
        virtualDisplay?.release()
        imageReader?.close()
        mediaProjection?.unregisterCallback(projectionCallback)
        mediaProjection?.stop()
        textRecognizer.close()
        tts.stop()
        tts.shutdown()
        isRunning = false
        stopForeground(STOP_FOREGROUND_REMOVE)
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
