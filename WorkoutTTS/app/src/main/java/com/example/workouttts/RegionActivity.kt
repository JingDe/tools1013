package com.example.workouttts

import android.app.Activity
import android.content.Intent
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Bundle
import android.view.MotionEvent
import android.view.View
import androidx.appcompat.app.AppCompatActivity

/**
 * 透明全屏 Activity：用户在屏幕上拖一个框，框住 B 站视频右上角的动作名。
 * 松手即把区域（屏幕像素坐标）回传给 MainActivity。
 */
class RegionActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(SelectionView(this))
    }

    private inner class SelectionView(context: android.content.Context) : View(context) {
        private var startX = 0f
        private var startY = 0f
        private var currentX = 0f
        private var currentY = 0f
        private var drawing = false

        private val stroke = Paint().apply {
            color = Color.RED
            style = Paint.Style.STROKE
            strokeWidth = 4f
        }
        private val fill = Paint().apply {
            color = Color.argb(60, 255, 255, 255)
            style = Paint.Style.FILL
        }

        override fun onDraw(canvas: Canvas) {
            super.onDraw(canvas)
            if (drawing) {
                val left = minOf(startX, currentX)
                val top = minOf(startY, currentY)
                val right = maxOf(startX, currentX)
                val bottom = maxOf(startY, currentY)
                canvas.drawRect(left, top, right, bottom, fill)
                canvas.drawRect(left, top, right, bottom, stroke)
            }
        }

        override fun onTouchEvent(event: MotionEvent): Boolean {
            when (event.action) {
                MotionEvent.ACTION_DOWN -> {
                    startX = event.x
                    startY = event.y
                    currentX = event.x
                    currentY = event.y
                    drawing = true
                }
                MotionEvent.ACTION_MOVE -> {
                    currentX = event.x
                    currentY = event.y
                    invalidate()
                }
                MotionEvent.ACTION_UP -> {
                    drawing = false
                    val left = minOf(startX, currentX)
                    val top = minOf(startY, currentY)
                    val right = maxOf(startX, currentX)
                    val bottom = maxOf(startY, currentY)
                    val result = Intent()
                    if (right - left > 20 && bottom - top > 20) {
                        result.putExtra("left", left.toInt())
                        result.putExtra("top", top.toInt())
                        result.putExtra("right", right.toInt())
                        result.putExtra("bottom", bottom.toInt())
                        setResult(Activity.RESULT_OK, result)
                    } else {
                        setResult(Activity.RESULT_CANCELED)
                    }
                    finish()
                }
            }
            return true
        }
    }
}
