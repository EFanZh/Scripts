function With-Env {
    param ($items, $action)

    $saved = @{}

    foreach ($item in $items.GetEnumerator()) {
        $saved.Add($item.Key, (Get-Item "Env:\$($item.Key)" -ErrorAction Ignore).Value)
        Set-Item -Path "Env:\$($item.Key)" -Value $item.Value
    }

    try {
        & $action
    } finally {
        foreach ($item in $saved.GetEnumerator()) {
            Set-Item -Path "Env:\$($item.Key)" -Value $item.Value
        }
    }
}
