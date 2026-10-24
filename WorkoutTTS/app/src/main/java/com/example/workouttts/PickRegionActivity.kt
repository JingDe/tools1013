package com.example.workouttts

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.RectF
import android.net.Uri
import android.os.Bundle
import android.provider.MediaStore
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.FrameLayout
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

/**
 * 基于截图的选区域：用户用系统截图截一张 B 站视频画面（动作名正显示），
 * 在这里从相册选这张图，直接在截图上拖框圈住动作名。
 *
 * 关键点：手机截图的像素尺寸通常等于屏幕真实分辨率，因此截图上画出的坐标
 * 约等于录屏裁剪所用的绝对屏幕坐标（这里再按 displayMetrics 做一次缩放校准，
 * 兼容截图分辨率与屏幕分辨率不一致的情况）。无需“在其他应用上层显示”权限。
 */
class PickRegionActivity : AppCompatActivity() {

    private lateinit var pickLauncher: androidx.activity.result.ActivityResultLauncher<Intent>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pickLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            if (result.resultCode == RESULT_OK && result.data?.data != null) {
                loadBitmap(result.data!!.data!!)
            } else {
                finish()
            }
        }
        openPicker()
    }

    private fun openPicker() {
        try {
            pickLauncher.launch(Intent(Intent.ACTION_PICK, MediaStore.Images.Media.EXTERNAL_CONTENT_URI))
        } catch (e: Exception) {
            try {
                pickLauncher.launch(Intent(Intent.ACTION_GET_CONTENT).apply { type = "image/*" })
            } catch (e2: Exception) {
                Toast.makeText(this, "无法打开图片选择器", Toast.LENGTH_SHORT).show()
                finish()
            }
        }
    }

    private fun loadBitmap(uri: Uri) {
        try {
            val opts = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, opts) }
            val w = opts.outWidth
            val h = opts.outHeight
            var scale = 1
            while (w / scale > 3000 || h / scale > 3000) scale *= 2
            val opts2 = BitmapFactory.Options().apply { inSampleSize = scale }
            val bmp = contentResolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, opts2) }
            if (bmp == null) {
                Toast.makeText(this, "图片读取失败", Toast.LENGTH_SHORT).show()
                finish()
                return
            }
            buildUi(bmp)
        } catch (e: Exception) {
            Toast.makeText(this, "读取失败: ${e.message}", Toast.LENGTH_SHORT).show()
            finish()
        }
    }

    private fun buildUi(bmp: Bitmap) {
        val frame = FrameLayout(this)
        val draw = RegionDrawView(this, bmp)
        frame.addView(draw, FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT))

        val btnOk = Button(this).apply {
            text = "确定"
            setOnClickListener { confirm(draw.getRect()) }
        }
        val okLp = FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT).apply {
            gravity = Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            bottomMargin = 90
        }
        frame.addView(btnOk, okLp)

        val btnRetry = Button(this).apply {
            text = "重选图片"
            setOnClickListener { openPicker() }
        }
        val retryLp = FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT).apply {
            gravity = Gravity.BOTTOM or Gravity.END
            bottomMargin = 90
            rightMargin = 40
        }
        frame.addView(btnRetry, retryLp)

        setContentView(frame)
    }

    private fun confirm(rect: Rect?) {
        if (rect == null || rect.width() < 20 || rect.height() < 20) {
            Toast.makeText(this, "请先拖框圈住动作名", Toast.LENGTH_SHORT).show()
            return
        }
        val intent = Intent().apply {
            putExtra("left", rect.left); putExtra("top", rect.top)
            putExtra("right", rect.right); putExtra("bottom", rect.bottom)
        }
        setResult(RESULT_OK, intent)
        finish()
    }

    /** 全屏显示截图并按比例居中；用户在上面拖框，坐标换算成屏幕绝对坐标。 */
    private inner class RegionDrawView(context: android.content.Context, private val bmp: Bitmap) : View(context) {
        private var sx = 0f; private var sy = 0f; private var cx = 0f; private var cy = 0f
        private var drawing = false
        private var scale = 1f; private var offX = 0f; private var offY = 0f
        private val stroke = Paint().apply { color = Color.RED; style = Paint.Style.STROKE; strokeWidth = 4f }
        private val fill = Paint().apply { color = Color.argb(60, 255, 255, 255); style = Paint.Style.FILL }
        private val hint = Paint().apply { color = Color.WHITE; textSize = 42f; isFakeBoldText = true }

        override fun onSizeChanged(w: Int, h: Int, ow: Int, oh: Int) {
            super.onSizeChanged(w, h, ow, oh)
            scale = minOf(w * 1f / bmp.width, h * 1f / bmp.height)
            offX = (w - bmp.width * scale) / 2f
            offY = (h - bmp.height * scale) / 2f
        }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            canvas.drawColor(Color.BLACK)
            canvas.drawBitmap(bmp, null, RectF(offX, offY, offX + bmp.width * scale, offY + bmp.height * scale), null)
            if (drawing) {
                val l = minOf(sx, cx); val t = minOf(sy, cy); val r = maxOf(sx, cx); val b = maxOf(sy, cy)
                canvas.drawRect(l, t, r, b, fill)
                canvas.drawRect(l, t, r, b, stroke)
            }
            canvas.drawText("拖框圈住动作名（别框倒计时数字），再点“确定”", 40f, 70f, hint)
        }

        override fun onTouchEvent(ev: MotionEvent): Boolean {
            when (ev.action) {
                MotionEvent.ACTION_DOWN -> { sx = ev.x; sy = ev.y; cx = ev.x; cy = ev.y; drawing = true }
                MotionEvent.ACTION_MOVE -> { cx = ev.x; cy = ev.y; invalidate() }
                MotionEvent.ACTION_UP -> { cx = ev.x; cy = ev.y; drawing = false }
            }
            return true
        }

        /** 把拖框的视图坐标换算成绝对屏幕坐标（按截图与屏幕分辨率缩放校准）。 */
        fun getRect(): Rect? {
            val l = minOf(sx, cx); val t = minOf(sy, cy); val r = maxOf(sx, cx); val b = maxOf(sy, cy)
            if (r - l < 20 || b - t < 20) return null
            val sw = resources.displayMetrics.widthPixels
            val sh = resources.displayMetrics.heightPixels
            val fx = sw * 1f / bmp.width
            val fy = sh * 1f / bmp.height
            val bx = ((l - offX) / scale * fx).toInt().coerceIn(0, sw)
            val by = ((t - offY) / scale * fy).toInt().coerceIn(0, sh)
            val bx2 = ((r - offX) / scale * fx).toInt().coerceIn(0, sw)
            val by2 = ((b - offY) / scale * fy).toInt().coerceIn(0, sh)
            return Rect(bx, by, bx2, by2)
        }
    }
}
