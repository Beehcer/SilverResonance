@echo off
REM 一键打包白银三频共振面板为单个 exe
REM 双击运行本文件即可。首次会自动安装 pyinstaller。

cd /d "%~dp0"
echo ============================================
echo   白银三频共振面板 - 打包工具
echo ============================================

echo [1/3] 检查 PyInstaller...
python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
  echo   未安装，正在安装 pyinstaller...
  python -m pip install pyinstaller
)

echo [2/3] 开始打包（首次较慢，请耐心等待）...
python -m PyInstaller build.spec --noconfirm --clean

echo [3/3] 完成！
echo.

REM 清理 dist 里的账号配置，避免把你的账号打包分发出去（导致同账号多设备冲突→卡在"拉取K线"）
if exist "dist\user_config.json" del /q "dist\user_config.json"

echo exe 位置： %~dp0dist\SilverResonance.exe
echo 分发时【只拷这一个 exe】，不要带 user_config.json。
echo 对方首次运行会填自己的天勤账号（同一账号不能多台电脑同时登录）。
echo.
pause
