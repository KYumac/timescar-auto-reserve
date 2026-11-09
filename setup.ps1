$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root
function Step($m) { Write-Host ""; Write-Host "==== $m" -ForegroundColor Green }

Step "1/5 Python を確認"
$py = $null
foreach ($c in @("py -3.12", "py -3", "python")) {
  try { $v = & cmd /c "$c --version" 2>$null; if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(1[0-9])") { $py = $c; break } } catch {}
}
if (-not $py) {
  Write-Host "Python が無いのでインストールします（数分かかります）"
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  $env:Path = [Environment]::GetEnvironmentVariable("Path","User") + ";" + [Environment]::GetEnvironmentVariable("Path","Machine")
  $py = "py -3.12"
}
Write-Host "使用: $py"

Step "2/5 必要な部品をインストール（初回は数分かかります）"
if (-not (Test-Path "$Root\.venv")) { & cmd /c "$py -m venv .venv" }
$Python  = "$Root\.venv\Scripts\python.exe"
$PythonW = "$Root\.venv\Scripts\pythonw.exe"
& $Python -m pip install -q --upgrade pip
& $Python -m pip install -q -r requirements.txt
& $Python -m playwright install chromium

Step "3/5 タイムズの会員番号とパスワードを登録"
Write-Host "Windows の資格情報マネージャーに保存します（このPCの外には出ません）"
$id = Read-Host "会員番号（10桁・ハイフンなし）"
$sec = Read-Host "パスワード" -AsSecureString
$pw = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
$env:TC_ID = $id; $env:TC_PW = $pw
& $Python -c "import os,sys; sys.path.insert(0,'src'); import settings; settings.save_credentials(os.environ['TC_ID'].replace('-','').strip(), os.environ['TC_PW'])"
Remove-Item Env:TC_ID, Env:TC_PW
$pw = $null

if (-not (Test-Path "$Root\settings.json")) {
  [IO.File]::WriteAllText("$Root\settings.json", '{"ntfy_topic": "__TOPIC__"}')
}

Step "4/5 自動実行を登録（タスクスケジューラ）"
$set = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$who = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
function Act($a) { New-ScheduledTaskAction -Execute $PythonW -Argument "`"$Root\src\main.py`" $a" -WorkingDirectory $Root }
function Reg($name, $trig, $arg) {
  Register-ScheduledTask -TaskPath "\Timescar\" -TaskName $name -Action (Act $arg) -Trigger $trig -Settings $set -Principal $who -Force | Out-Null
  Write-Host "  登録: $name"
}
Reg "1_予約（毎日23時50分）" (New-ScheduledTaskTrigger -Daily -At "23:50") "reserve --wait-open"
Reg "2_前日の確認通知（金土20時47分）" (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday,Saturday -At "20:47") "remind"
Reg "3_自動キャンセル（土日朝）" @(
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday,Sunday -At "08:05"),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday,Sunday -At "08:25"),
  (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday,Sunday -At "08:45")) "auto-cancel"
Get-ScheduledTask -TaskPath "\Timescar\" -ErrorAction SilentlyContinue | Where-Object TaskName -like "4_*" | Unregister-ScheduledTask -Confirm:$false
$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
Reg "4_iPhoneからの操作（1分おき）" $t "poll"
try {
  powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>$null
  powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1 2>$null
  powercfg /SETACTIVE SCHEME_CURRENT 2>$null
} catch {}

Step "5/5 ログイン（ここだけ手で行います）"
Write-Host "ブラウザが開きます。会員番号とパスワードは入力済みなので、「ログイン」ボタンを押してください。"
Write-Host "マイページが表示されたら、ブラウザは自動で閉じます。"
& $Python "$Root\src\main.py" login
if ($LASTEXITCODE -ne 0) {
  Write-Host ""
  Write-Host "ログインできませんでした。この画面をスクリーンショットして Claude に送ってください。" -ForegroundColor Red
  exit 1
}
& $Python "$Root\src\main.py" test
if ($LASTEXITCODE -eq 0) {
  Write-Host ""
  Write-Host "完了しました。iPhone に「接続テスト成功」の通知が届きます。" -ForegroundColor Green
} else {
  Write-Host ""
  Write-Host "接続テストに失敗しました。この画面をスクリーンショットして Claude に送ってください。" -ForegroundColor Red
}
