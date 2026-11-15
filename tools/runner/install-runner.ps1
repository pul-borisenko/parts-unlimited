<#
.SYNOPSIS
    Установка self-hosted раннера GitHub Actions на Windows (ЛР 4.0).

.DESCRIPTION
    Аналог установки локального агента Azure Pipelines:
      1. проверка требований (64-разрядная Windows 10/11 или Server 2016+, PowerShell 5.1+);
      2. каталог раннера без пробелов в пути, с доступом только для администраторов, SYSTEM
         и учётной записи, от которой работает раннер;
      3. загрузка дистрибутива с проверкой контрольной суммы SHA-256;
      4. регистрация по одноразовому токену (действует 1 час) с метками — аналог возможностей агента;
      5. по желанию — установка службы Windows под ограниченной учётной записью NETWORK SERVICE.

    Токен регистрации берётся на странице репозитория:
    Settings → Actions → Runners → New self-hosted runner (строка ./config.cmd ... --token XXXX).
    Там же указана контрольная сумма архива для параметра -Sha256.

.EXAMPLE
    # PowerShell от имени администратора:
    .\install-runner.ps1 -Url https://github.com/pul-borisenko/parts-unlimited -AsService -Sha256 <хэш>

.EXAMPLE
    # Интерактивный режим без службы (как .\run.cmd в методичке):
    .\install-runner.ps1 -Url https://github.com/pul-borisenko/parts-unlimited -Token <токен>
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $Url,
    [string] $Token,
    [string] $Name = $env:COMPUTERNAME,
    [string[]] $Labels = @('pul-build', 'dotnet'),
    [string] $Directory = 'C:\actions-runner',
    [string] $Version,
    [string] $Sha256,
    [switch] $AsService,
    [string] $ServiceAccount,
    [string] $RunnerGroup,
    [switch] $Replace
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # без индикатора загрузка в Windows PowerShell 5.1 идёт в разы быстрее
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Test-Administrator {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

Write-Host '[1/5] Проверка требований'
if (-not [Environment]::Is64BitOperatingSystem) { throw 'Нужна 64-разрядная Windows 10/11 или Windows Server 2016 и новее.' }
if ([Environment]::OSVersion.Version.Major -lt 10) { throw "Версия Windows $([Environment]::OSVersion.Version) не поддерживается раннером." }
if ($PSVersionTable.PSVersion.Major -lt 5) { throw 'Нужен PowerShell 5.1 или новее.' }
if ($Host.Name -like '*ISE*') { throw 'Не запускайте установку из PowerShell ISE — используйте обычное окно PowerShell.' }
if ($AsService -and -not (Test-Administrator)) { throw 'Для установки службы запустите PowerShell от имени администратора.' }
if ($Directory -match '\s') { throw 'Путь к каталогу раннера не должен содержать пробелов.' }
if (Test-Path (Join-Path $Directory '.runner')) {
    throw "В $Directory уже настроен раннер. Удалите его: cd $Directory; .\config.cmd remove --token <токен удаления>"
}
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }

if (-not $Version) {
    try {
        $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/actions/runner/releases/latest' -Headers @{ 'User-Agent' = 'install-runner' }
        $Version = $release.tag_name.TrimStart('v')
    } catch {
        $Version = '2.338.0'
        Write-Warning "Не удалось узнать последнюю версию раннера, используется $Version"
    }
}
if (-not $Token) {
    $secure = Read-Host 'Токен регистрации раннера (ввод скрыт)' -AsSecureString
    $Token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}

Write-Host "[2/5] Каталог $Directory и права доступа"
New-Item -ItemType Directory -Force -Path $Directory | Out-Null
if (Test-Administrator) {
    # Наследование отключено: полный доступ у Администраторов (S-1-5-32-544) и SYSTEM (S-1-5-18),
    # изменение — только у учётной записи, от которой работает раннер. SID не зависят от языка Windows.
    if ($AsService) {
        $runnerIdentity = if ($ServiceAccount) { $ServiceAccount } else { '*S-1-5-20' }   # NETWORK SERVICE
    } else {
        $runnerIdentity = "$env:USERDOMAIN\$env:USERNAME"
    }
    & icacls $Directory /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' "${runnerIdentity}:(OI)(CI)M" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls завершился с кодом $LASTEXITCODE" }
} else {
    Write-Warning 'Права на каталог не ограничены: для этого запустите скрипт от имени администратора.'
}

Write-Host "[3/5] Загрузка раннера $Version ($arch)"
$fileName = "actions-runner-win-$arch-$Version.zip"
$zip = Join-Path $env:TEMP $fileName
Invoke-WebRequest -Uri "https://github.com/actions/runner/releases/download/v$Version/$fileName" -OutFile $zip -UseBasicParsing
$hash = (Get-FileHash -Path $zip -Algorithm SHA256).Hash
if ($Sha256) {
    if ($hash -ne $Sha256.Trim().ToUpperInvariant()) { Remove-Item $zip; throw "Контрольная сумма не совпадает: $hash" }
    Write-Host '      Контрольная сумма совпадает'
} else {
    Write-Warning "Контрольная сумма не проверена (SHA-256 архива: $hash). Сверьте её со страницей New self-hosted runner."
}
Expand-Archive -Path $zip -DestinationPath $Directory -Force
Remove-Item $zip

Write-Host '[4/5] Регистрация раннера'
$configArgs = @('--unattended', '--url', $Url, '--token', $Token, '--name', $Name,
                '--labels', ($Labels -join ','), '--work', '_work')
if ($RunnerGroup) { $configArgs += @('--runnergroup', $RunnerGroup) }
if ($Replace) { $configArgs += '--replace' }
if ($AsService) {
    $configArgs += '--runasservice'
    if ($ServiceAccount) {
        $credential = Get-Credential -UserName $ServiceAccount -Message 'Пароль учётной записи, от которой будет работать служба'
        $configArgs += @('--windowslogonaccount', $credential.UserName, '--windowslogonpassword', $credential.GetNetworkCredential().Password)
    }
}
Push-Location $Directory
try {
    & .\config.cmd @configArgs
    if ($LASTEXITCODE -ne 0) { throw "config.cmd завершился с кодом $LASTEXITCODE" }
} finally {
    Pop-Location
    $Token = $null
}

Write-Host '[5/5] Готово'
if ($AsService) {
    Get-Service 'actions.runner.*' | Format-Table -AutoSize Name, Status, StartType
    Write-Host 'Управление службой: Start-Service / Stop-Service / Get-Service "actions.runner.*"'
} else {
    Write-Host "Запуск в интерактивном режиме: cd $Directory; .\run.cmd   (остановка — Ctrl+C)"
}
Write-Host "Метки раннера: self-hosted, Windows, X64, $($Labels -join ', ')"
Write-Host "Удаление раннера: cd $Directory; .\config.cmd remove --token <токен удаления из настроек раннера>"
