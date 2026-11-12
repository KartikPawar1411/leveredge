param([string]$Path)
# Recalculates the exported workbook in desktop Excel and prints the Model Checks sheet.
$full = (Resolve-Path $Path).Path
$xl = New-Object -ComObject Excel.Application
$xl.Visible = $false; $xl.DisplayAlerts = $false
try {
  $wb = $xl.Workbooks.Open($full)
  $xl.CalculateFull()
  $ws = $wb.Worksheets.Item("Model Checks")
  $used = $ws.UsedRange
  for ($r = 1; $r -le $used.Rows.Count; $r++) {
    $a = $ws.Cells.Item($r,1).Text; if ($a) { "{0,-58} {1,14} {2,14} {3,14}" -f $a, $ws.Cells.Item($r,3).Text, $ws.Cells.Item($r,4).Text, $ws.Cells.Item($r,5).Text }
  }
  $errs = 0
  foreach ($s in $wb.Worksheets) {
    $u = $s.UsedRange
    try { $e = $u.SpecialCells(-4123, 16); $errs += $e.Count; "ERROR CELLS on $($s.Name): $($e.Address())" } catch {}
  }
  "Formula error cells: $errs"
  $wb.Close($false)
} finally { $xl.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($xl) }
