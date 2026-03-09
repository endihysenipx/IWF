param(
    [string]$PdfPath = "C:\Users\Admin\Documents\GitHub\IWF\temp_incoming_ab.pdf",
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [string]$BearerToken = "local-token",
    [string]$CallbackUrl = "https://httpbin.org/post",
    [string]$CorrelationPrefix = "batch-run",
    [int]$Copies = 2,
    [int]$PollSeconds = 2,
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PdfPath)) {
    throw "PDF not found: $PdfPath"
}

if ($Copies -lt 1) {
    throw "Copies must be at least 1."
}

$jobs = @()

for ($i = 1; $i -le $Copies; $i++) {
    $submitArgs = @(
        "-sS",
        "-X", "POST",
        "$ApiBaseUrl/v1/document-jobs",
        "-H", "Authorization: Bearer $BearerToken",
        "-F", "file=@$PdfPath;type=application/pdf",
        "-F", "callback_url=$CallbackUrl",
        "-F", "correlation_id=$CorrelationPrefix-$i"
    )

    $submitResponse = & curl.exe @submitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Submission failed for copy $i."
    }

    $submittedJob = $submitResponse | ConvertFrom-Json
    if (-not $submittedJob.job_id) {
        throw "API response for copy $i did not include a job_id. Response: $submitResponse"
    }

    $jobs += [pscustomobject]@{
        Index = $i
        JobId = $submittedJob.job_id
        Status = $submittedJob.status
        Final = $null
    }

    Write-Host "Submitted copy $i as job $($submittedJob.job_id)"
}

Write-Host ""
Write-Host "Polling $Copies jobs"

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)

while ((Get-Date) -lt $deadline) {
    $allDone = $true

    foreach ($job in $jobs) {
        if ($job.Final) {
            continue
        }

        $statusArgs = @(
            "-sS",
            "$ApiBaseUrl/v1/document-jobs/$($job.JobId)",
            "-H", "Authorization: Bearer $BearerToken"
        )

        $statusResponse = & curl.exe @statusArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Status check failed for job $($job.JobId)."
        }

        $statusJob = $statusResponse | ConvertFrom-Json
        $job.Status = $statusJob.status
        Write-Host "copy=$($job.Index) job=$($job.JobId) status=$($job.Status)"

        if ($job.Status -in @("completed", "failed")) {
            $job.Final = $statusJob
        }
        else {
            $allDone = $false
        }
    }

    if ($allDone) {
        break
    }

    Write-Host ""
    Start-Sleep -Seconds $PollSeconds
}

$unfinished = $jobs | Where-Object { -not $_.Final }
if ($unfinished) {
    $ids = ($unfinished | ForEach-Object { $_.JobId }) -join ", "
    throw "Timed out waiting for jobs: $ids"
}

$totalCost = 0.0

foreach ($job in $jobs) {
    $result = $job.Final

    Write-Host ""
    Write-Host "Job result"
    Write-Host "copy: $($job.Index)"
    Write-Host "job_id: $($result.job_id)"
    Write-Host "status: $($result.status)"
    Write-Host "document_number: $($result.document_number)"
    Write-Host "order_document_number: $($result.order_document_number)"
    Write-Host "output_blob_name: $($result.output_blob_name)"

    if ($result.billing_summary) {
        $billing = $result.billing_summary
        $jobCost = [double]$billing.costs.total_estimated_usd
        $totalCost += $jobCost

        Write-Host ""
        Write-Host "Billing"
        Write-Host "total_estimated_usd: $($billing.costs.total_estimated_usd)"
        Write-Host "openai_input_usd: $($billing.costs.openai_input_usd)"
        Write-Host "openai_output_usd: $($billing.costs.openai_output_usd)"
        Write-Host "pdf_pages: $($billing.usage.pdf_pages)"
        Write-Host "prompt_tokens: $($billing.usage.prompt_tokens)"
        Write-Host "completion_tokens: $($billing.usage.completion_tokens)"
        Write-Host "processing_seconds: $($billing.processing_seconds)"
        Write-Host ""
        $billing | ConvertTo-Json -Depth 10
    }
    else {
        Write-Host ""
        Write-Host "No billing summary returned."
    }
}

Write-Host ""
Write-Host "Combined total_estimated_usd: $([Math]::Round($totalCost, 8))"
