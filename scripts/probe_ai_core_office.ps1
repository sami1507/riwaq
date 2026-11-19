param([ValidateSet('Word','Excel')][string]$Kind, [string]$InputPath, [string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
function SourceHash([string]$Path) {
    $digest = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($digest.ComputeHash([IO.File]::ReadAllBytes($Path))).Replace('-', '').ToLowerInvariant() }
    finally { $digest.Dispose() }
}
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$reportRoot = [IO.Path]::GetFullPath((Join-Path $workspace 'reports')) + [IO.Path]::DirectorySeparatorChar
$source = [IO.Path]::GetFullPath($InputPath)
$target = [IO.Path]::GetFullPath($OutputDirectory)
if (-not $source.StartsWith($reportRoot, [StringComparison]::OrdinalIgnoreCase) -or -not $target.StartsWith($reportRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Synthetic reports paths only' }
if ((Get-Item -LiteralPath $source).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked fixture refused' }
$processName = if ($Kind -eq 'Word') {'WINWORD'} else {'EXCEL'}
if (Get-Process -Name $processName -ErrorAction SilentlyContinue) { throw 'An existing Office process is present; refusing to attach or close it' }
New-Item -ItemType Directory -Path $target -ErrorAction Stop | Out-Null
$reportPath = Join-Path $target 'receipt.json'
$record = [ordered]@{application=$Kind; synthetic=$true; status='blocked'; phase='starting_private_COM_instance'; source_sha256=(SourceHash $source); native_rendered=$false}
$record | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $reportPath -Encoding utf8
$office = $null; $document = $null
try {
    $office = New-Object -ComObject ($Kind + '.Application')
    $office.Visible = $false
    $office.DisplayAlerts = if ($Kind -eq 'Word') {0} else {$false}
    $office.AutomationSecurity = 3
    $record['version'] = $office.Version
    $record['phase'] = 'opening_own_disposable_copy'
    $record | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $reportPath -Encoding utf8
    $copy = Join-Path $target ([IO.Path]::GetFileName($source))
    Copy-Item -LiteralPath $source -Destination $copy -ErrorAction Stop
    if ($Kind -eq 'Word') {
        $document = $office.Documents.Open($copy, $false, $false, $false)
        $record['table_count'] = $document.Tables.Count
        $record['original_cell'] = $document.Tables.Item(1).Cell(4,3).Range.Text.Trim([char]13,[char]7)
        if ($record['original_cell'] -ne '20') { throw 'Unexpected synthetic cell; no edit performed' }
        $document.Tables.Item(1).Cell(4,3).Range.Text = '25'
        $record['edited_cell'] = $document.Tables.Item(1).Cell(4,3).Range.Text.Trim([char]13,[char]7)
        $document.Tables.Item(1).Cell(4,3).Range.Text = '20'
        $document.Save()
        $document.ExportAsFixedFormat((Join-Path $target 'arabic-table.pdf'), 17)
        $record['page_count'] = $document.ComputeStatistics(2)
        $record['text'] = $document.Content.Text
        $record['native_rendered'] = (Test-Path -LiteralPath (Join-Path $target 'arabic-table.pdf'))
        $document.Close(0); [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document); $document=$null
        $document = $office.Documents.Open($copy, $false, $true, $false)
        $record['reopened_cell'] = $document.Tables.Item(1).Cell(4,3).Range.Text.Trim([char]13,[char]7)
    } else {
        $document = $office.Workbooks.Open($copy, 0, $false)
        $sheet = $document.Worksheets.Item(1)
        $record['chart_count'] = $sheet.ChartObjects().Count
        $record['series'] = @()
        foreach ($chartObject in $sheet.ChartObjects()) {
            $chart = $chartObject.Chart
            foreach ($series in $chart.SeriesCollection()) { $record['series'] += @{name=$series.Name;formula=$series.Formula;values=@($series.Values);categories=@($series.XValues)} }
            $rendered = $chart.Export((Join-Path $target ('chart-' + $chartObject.Index + '.png')), 'PNG', $false)
            $record['native_rendered'] = [bool]$rendered
        }
        $record['calculation_state'] = $office.CalculationState
    }
    $record['status'] = if ($record['native_rendered']) {'rendered_pending_visual_review'} else {'blocked'}
    $record['phase'] = 'completed'
} catch {
    $record['error_type'] = $_.Exception.GetType().FullName
    $record['error_code'] = $_.Exception.HResult
    $record['error'] = $_.Exception.Message
} finally {
    if ($null -ne $document) { try {$document.Close($false)} catch {}; [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document) }
    if ($null -ne $office) { try {$office.Quit()} catch {}; [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($office) }
    $record['source_unchanged'] = $record['source_sha256'] -eq (SourceHash $source)
    $record | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $reportPath -Encoding utf8
}
