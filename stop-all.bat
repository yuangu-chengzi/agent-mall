@echo off
setlocal

REM ============================================================================
REM  停止 start-all.bat 启动的那 6 个服务
REM
REM  按**窗口标题**精确匹配（start-all 给每个窗口起了 mall-N-xxx 的标题），
REM  所以不会误杀你自己开的其他 java / cmd 窗口。
REM ============================================================================

echo 停止 waynboot-mall 的 6 个服务...
echo.

for %%S in ("mall-1-Redis" "mall-2-Elasticsearch" "mall-3-RabbitMQ" "mall-4-mobile-api-82" "mall-5-message-consumer-85" "mall-6-Agent-8300") do (
  taskkill /FI "WINDOWTITLE eq %%~S*" /T /F >nul 2>&1
  if errorlevel 1 (
    echo   [ ] %%~S  没在运行
  ) else (
    echo   [√] %%~S  已停止
  )
)

echo.
echo 注意：RabbitMQ 的 erl.exe 有时会残留，必要时用任务管理器结束。
echo.
pause
