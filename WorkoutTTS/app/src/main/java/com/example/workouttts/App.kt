package com.example.workouttts

import android.app.Application
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import java.io.PrintWriter
import java.io.StringWriter

/**
 * 全局 Application：在进程最早启动时就挂上未捕获异常处理器。
 * 这样无论崩溃发生在 Activity 还是前台 Service（同一进程），都能：
 *   1) 把堆栈写入 app 私有目录 crash.log（追加）
 *   2) 把“最后一次崩溃”存进 SharedPreferences，下次打开 App 直接显示在界面上
 *   3) 弹一个 Toast 提示
 * 目的：无电脑联调时，也能一次定位闪退原因。
 */
class App : Application() {

    override fun onCreate() {
        super.onCreate()
        val defaultHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            val sw = StringWriter()
            throwable.printStackTrace(PrintWriter(sw))
            val stack = sw.toString()
            val msg = "${throwable.javaClass.simpleName}: ${throwable.message ?: "无消息"}"
            try {
                val prefs = getSharedPreferences("crash_prefs", Context.MODE_PRIVATE)
                val prev = prefs.getString("last_crash", "") ?: ""
                // 最新的放在最前面，避免无限累积
                val combined = if (prev.isBlank()) msg else "$msg\n----------\n$prev"
                prefs.edit().putString("last_crash", combined.take(4000)).apply()
                openFileOutput("crash.log", Context.MODE_APPEND).use { it.write(stack.toByteArray()) }
            } catch (_: Exception) {
                // 忽略写入失败
            }
            // 主线程弹 Toast（进程即将死，给 2.5s 让它有机会显示）
            Handler(Looper.getMainLooper()).post {
                try {
                    Toast.makeText(this, "崩溃: $msg", Toast.LENGTH_LONG).show()
                } catch (_: Exception) {
                }
            }
            try {
                Thread.sleep(2500)
            } catch (_: InterruptedException) {
            }
            defaultHandler?.uncaughtException(thread, throwable)
        }
    }
}
