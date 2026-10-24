@echo off
REM ============================================================
REM  WorkoutTTS - build debug APK without Android Studio
REM  Double-click this file to build. No IDE needed.
REM  Output APK: app\build\outputs\apk\debug\app-debug.apk
REM ============================================================
setlocal

REM --- JDK 17 (Gradle 8.9 needs Java 8-22; do NOT use JDK 25) ---
set "JAVA_HOME=%USERPROFILE%\.jdks\ms-17.0.20.1"

REM --- Android SDK location ---
set "ANDROID_HOME=D:\Android\AndroidSDK"

REM --- Gradle cache (downloaded Gradle lives here) ---
set "GRADLE_USER_HOME=%USERPROFILE%\.gradle"

REM --- go to this script's folder ---
cd /d "%~dp0"

REM --- find cached Gradle 8.9 so we can skip any download ---
set "GRADLE_BIN="
for /d %%G in ("%GRADLE_USER_HOME%\wrapper\dists\gradle-8.9-bin\*") do (
  if exist "%%G\gradle-8.9\bin\gradle.bat" set "GRADLE_BIN=%%G\gradle-8.9\bin\gradle.bat"
)

if defined GRADLE_BIN (
  echo [WorkoutTTS] Using cached Gradle: %GRADLE_BIN%
  call "%GRADLE_BIN%" assembleDebug --no-daemon
) else (
  echo [WorkoutTTS] Cached Gradle not found, using project gradlew (first run downloads Gradle 8.9)...
  call gradlew.bat assembleDebug --no-daemon
)

echo.
if exist "app\build\outputs\apk\debug\app-debug.apk" (
  echo [WorkoutTTS] BUILD OK. APK file:
  echo   %~dp0app\build\outputs\apk\debug\app-debug.apk
) else (
  echo [WorkoutTTS] BUILD FAILED. Please check the messages above.
)
pause
