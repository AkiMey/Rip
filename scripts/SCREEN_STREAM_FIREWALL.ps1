$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ruleName = "RIP V6 Screen Stream 8766"
try {
    Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8766 -Profile Any | Out-Null
    Write-Host "[OK] RIP canli ekran TCP 8766 firewall izni hazir." -ForegroundColor Green
    exit 0
} catch {
    Write-Host "[HATA] TCP 8766 firewall izni eklenemedi: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
