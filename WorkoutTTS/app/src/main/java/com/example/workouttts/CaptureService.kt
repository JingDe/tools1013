package com.example.workouttts

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.graphics.Rect
import android.media.ImageReader
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.speech.tts.TextToSpeech
import android.util.DisplayMetrics
import android.view.DisplayManager
import androidx.core.app.NotificationCompat
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.TextRecognizer
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import java.util.Locale
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/**
 * 前台服务：拿到录屏权限后，每 ~0.8s 截取全屏 → 裁剪出 ROI → ML Kit 识别 →
 * 与上一帧比对，发现动作名变了就用系统 TTS 念出来（离线英文）。
 */
class CaptureService : Service() {

    companion object {
        var isRunning = false
        const val ACTION_TEST = "TEST_OCR"            // MainActivity 发来：只识别一次、不朗读
        const val ACTION_RESULT = "com.example.workouttts.OCR_RESULT" // 广播识别结果给 MainActivity
    }

    private lateinit var projectionManager: MediaProjectionManager
    private var mediaProjection: android.media.projection.MediaProjection? = null
    private var virtualDisplay: android.hardware.display.VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private lateinit var textRecognizer: TextRecognizer
    private lateinit var tts: TextToSpeech
    private lateinit var scheduler: ScheduledExecutorService
    private val mainHandler = Handler(Looper.getMainLooper())

    private var region: Rect? = null
    private var prevText = ""
    private var lastSpoken = ""
    private var busy = false   // 防止上一帧 OCR 没返回就又取新帧

    override fun onCreate() {
        super.onCreate()
        projectionManager = getSystemService(MediaProjectionManager::class.java)
        textRecognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
        tts = TextToSpeech(this) { status ->
            if (status == TextToSpeech.SUCCESS) {
                // 设为英文；若设备没装英文语音包，需在系统设置里下载
                tts.setLanguage(Locale.ENGLISH)
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
        return START_STICKY
    }

    private fun setupProjection(resultCode: Int, data: Intent) {
        mediaProjection = projectionManager.getMediaProjection(resultCode, data)
        val metrics: DisplayMetrics = resources.displayMetrics
        val width = metrics.widthPixels
        val height = metrics.heightPixels
        imageReader = ImageReader.newInstance(width, height, PixelFormat.RGBA_8888, 2)
        virtualDisplay = mediaProjection?.createVirtualDisplay(
            "WorkoutTTS", width, height, metrics.densityDpi,
            DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
            imageReader!!.surface, null, null
        )
        scheduler = Executors.newSingleThreadScheduledExecutor()
        // 启动后 0.5s 开始，之后每 0.8s 取一帧
        scheduler.scheduleWithFixedDelay({ poll(true) }, 500, 800, TimeUnit.MILLISECONDS)
    }

    /** 取一帧 → 裁剪 ROI → OCR。speak=false 时只识别不朗读（用于测试）。 */
    private fun poll(speak: Boolean) {
        if (busy) return
        val reader = imageReader ?: return
        val image = reader.acquireLatestImage() ?: return
        busy = true
        try {
            val bitmap = imageToBitmap(image)
            val roi = region
            val cropped = if (roi != null && roi.width() > 0 && roi.height() > 0) {
                Bitmap.createBitmap(bitmap, roi.left, roi.top, roi.width(), roi.height())
            } else {
                bitmap
            }
            val input = InputImage.fromBitmap(cropped, 0)
            textRecognizer.process(input)
                .addOnSuccessListener { visionText ->
                    handleResult(normalize(visionText.text), speak)
                    busy = false
                }
                .addOnFailureListener { busy = false }
        } catch (e: Exception) {
            busy = false
        } finally {
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
        return NotificationCompat.Builder(this, channelId)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.notification_text))
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .build()
    }

    override fun onDestroy() {
        super.onDestroy()
        scheduler.shutdown()
        virtualDisplay?.release()
        imageReader?.close()
        mediaProjection?.stop()
        textRecognizer.close()
        tts.stop()
        tts.shutdown()
        isRunning = false
        stopForeground(STOP_FOREGROUND_REMOVE)
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
