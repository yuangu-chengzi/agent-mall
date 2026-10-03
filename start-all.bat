@echo off
setlocal

REM ============================================================================
REM  waynboot-mall 本地一键启动 / 停止
REM
REM  用法：双击本文件启动（每个服务一个窗口）；stop-all.bat 停止
REM  日志：logs\1-redis.log ... logs\6-agent.log
REM
REM  写这个脚本踩过的坑，改之前先看：
REM    1. 含中文的 .bat 必须存成 GBK/ANSI，UTF-8 会被 cmd 解析成乱码。
REM    2. 别在 if(...) 代码块里展开系统 Path —— 它含有括号目录名，
REM       右括号会提前闭合代码块。变量统一在文件开头准备好。
REM    3. 服务一律用绝对路径调起，不依赖 cd 是否生效（cd 一旦悄悄失败，
REM       相对路径的可执行文件就报"不是内部或外部命令"，很难查）。
REM    4. 日志目录变量不能叫 LOGS：RabbitMQ 的启动脚本会读这个名字，
REM       把目录当成它的日志文件，直接 BOOT FAILED。
REM    5. 给 start 传带嵌套引号的命令（cmd /k "cd ... && prog ..."）在不同 cmd 上
REM       表现不一致。改成让子窗口用参数再调一次本脚本，命令写在标签里。
REM    6. 用脚本批量改本文件时要小心：Windows 路径里的反斜杠紧跟着数字或字母
REM       （比如 反斜杠+1-redis.log、反斜杠+redis-server.exe）很容易被当成
REM       转义序列写坏成一个控制字符。改完务必扫一遍有没有游离的控制字符。
REM ============================================================================

set "ROOT=%~dp0"
REM 本机私密配置（数据库口令等）单独放 local-env.bat，不进仓库
if exist "%ROOT%local-env.bat" call "%ROOT%local-env.bat"
set "REDIS_HOME=D:\redis\redis-8.10.2\Redis-8.10.2-Windows-x64-msys2"
set "ES_HOME=D:\elasticsearch\es\elasticsearch-7.17.29"
set "ERLANG_HOME=D:\rabbitmq\erlang"
set "RABBITMQ_HOME=D:\rabbitmq\server42\rabbitmq_server-4.2.9"
set "RABBITMQ_BASE=D:\rabbitmq\data"
set "UPLOAD_DIR=D:/waynshop/webp"
set "AGENT_DIR=%ROOT%waynboot-agent"
set "WAYN_LOGS=%ROOT%logs"
set "PATH=%ERLANG_HOME%\bin;%PATH%"

REM ---------- 子命令：每个服务在自己的窗口里跑这里的分支 ----------
if /i "%~1"=="redis" goto run_redis
if /i "%~1"=="es" goto run_es
if /i "%~1"=="rabbitmq" goto run_rabbitmq
if /i "%~1"=="mall" goto run_mall
if /i "%~1"=="consumer" goto run_consumer
if /i "%~1"=="agent" goto run_agent
goto main

:run_redis
cd /d "%REDIS_HOME%"
REM --save 不能省：redis.conf 没开持久化，重启一次登录态(Sa-Token)就全丢
"%REDIS_HOME%\redis-server.exe" redis.conf --save 3600 1 --save 300 100 --save 60 10000 > "%WAYN_LOGS%\1-redis.log" 2>&1
goto :eof

:run_es
call "%ES_HOME%\bin\elasticsearch.bat" > "%WAYN_LOGS%\2-elasticsearch.log" 2>&1
goto :eof

:run_rabbitmq
call "%RABBITMQ_HOME%\sbin\rabbitmq-server.bat" > "%WAYN_LOGS%\3-rabbitmq.log" 2>&1
goto :eof

:run_mall
java -jar "%ROOT%jars\waynboot-mobile-api.jar" > "%WAYN_LOGS%\4-mobile-api.log" 2>&1
goto :eof

:run_consumer
java -jar "%ROOT%jars\waynboot-message-consumer.jar" > "%WAYN_LOGS%\5-message-consumer.log" 2>&1
goto :eof

:run_agent
cd /d "%AGENT_DIR%"
"%AGENT_DIR%\.venv\Scripts\python.exe" -m uvicorn app.main:app --port 8300 > "%WAYN_LOGS%\6-agent.log" 2>&1
goto :eof

REM ============================================================================
:main
if not exist "%WAYN_LOGS%" mkdir "%WAYN_LOGS%"

echo ============================================================
echo  启动 waynboot-mall       日志：%WAYN_LOGS%
echo ============================================================
echo.
echo [1/2] 中间件...

if not exist "%REDIS_HOME%\redis-server.exe" (
  echo   [x] 找不到 Redis：%REDIS_HOME%
) else (
  start "mall-1-Redis" cmd /k "%~f0" redis
  echo   [OK] Redis             6379
)

if not exist "%ES_HOME%\bin\elasticsearch.bat" (
  echo   [x] 找不到 Elasticsearch：%ES_HOME%
) else (
  start "mall-2-Elasticsearch" cmd /k "%~f0" es
  echo   [OK] Elasticsearch     9200
)

if not exist "%RABBITMQ_HOME%\sbin\rabbitmq-server.bat" (
  echo   [x] 找不到 RabbitMQ：%RABBITMQ_HOME%
) else (
  start "mall-3-RabbitMQ" cmd /k "%~f0" rabbitmq
  echo   [OK] RabbitMQ          5672
)

echo.
echo   等中间件就绪（约 25 秒）...
ping -n 26 127.0.0.1 >nul

echo.
echo [2/2] 应用...

if not exist "%ROOT%jars\waynboot-mobile-api.jar" (
  echo   [x] 缺少 jars\waynboot-mobile-api.jar（先在根目录 mvn package -DskipTests）
) else (
  start "mall-4-mobile-api-82" cmd /k "%~f0" mall
  echo   [OK] mall 移动端       82   含 outbox relay（每 5 秒把本地消息投给 MQ）
)

if not exist "%ROOT%jars\waynboot-message-consumer.jar" (
  echo   [x] 缺少 jars\waynboot-message-consumer.jar
) else (
  start "mall-5-message-consumer-85" cmd /k "%~f0" consumer
  echo   [OK] 消息消费端       85   不起它订单永远停在未付款
)

if not exist "%AGENT_DIR%\.venv\Scripts\python.exe" (
  echo   [x] 找不到 Agent 虚拟环境：%AGENT_DIR%\.venv
) else (
  start "mall-6-Agent-8300" cmd /k "%~f0" agent
  echo   [OK] AI 客服 Agent    8300 商城页面也由它托管
)

echo.
echo ============================================================
echo  全部启动完毕。等 Java 应用起来（约 30 秒）后访问：
echo    商城前端  http://localhost:8300
echo    AI 客服   http://localhost:8300/chat
echo    探针      http://localhost:8300/readyz
echo.
echo  登录：手机号 13800000001 验证码 1234（开发环境固定）
echo  提示：下单后 1 分钟内要付款，否则系统自动取消
echo  哪个起不来看 logs 目录下对应日志
echo ============================================================
echo.
pause
