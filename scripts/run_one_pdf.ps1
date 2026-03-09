param(
    [string]$PdfPath = "C:\Users\Admin\Documents\GitHub\IWF\temp_incoming_ab.pdf",
    [string]$ApiBaseUrl = "http://127.0.0.1:8000",
    [string]$BearerToken = "local-token",
    [string]$CallbackUrl = "https://httpbin.org/post",
    [string]$CorrelationId = "manual-run",
    [int]$PollSeconds = 2,
    [int]$TimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $PdfPath)) {
    throw "PDF not found: $PdfPath"
}

$submitArgs = @(
    "-sS",
    "-X", "POST",
    "$ApiBaseUrl/v1/document-jobs",
    "-H", "Authorization: Bearer $BearerToken",
    "-F", "file=@$PdfPath;type=application/pdf",
    "-F", "callback_url=$CallbackUrl",
    "-F", "correlation_id=$CorrelationId"
)

$submitResponse = & curl.exe @submitArgs
if ($LASTEXITCODE -ne 0) {
    throw "Submission failed."
}

$submittedJob = $submitResponse | ConvertFrom-Json
$jobId = $submittedJob.job_id

if (-not $jobId) {
    throw "API response did not include a job_id. Response: $submitResponse"
}

Write-Host "Submitted job: $jobId"
Write-Host "Initial status: $($submittedJob.status)"

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$lastJob = $null

while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds $PollSeconds

    $statusArgs = @(
        "-sS",
        "$ApiBaseUrl/v1/document-jobs/$jobId",
        "-H", "Authorization: Bearer $BearerToken"
    )

    $statusResponse = & curl.exe @statusArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Status check failed for job $jobId."
    }

    $lastJob = $statusResponse | ConvertFrom-Json
    Write-Host "Status: $($lastJob.status)"

    if ($lastJob.status -in @("completed", "failed")) {
        break
    }
}

if ($null -eq $lastJob) {
    throw "No status response received for job $jobId."
}

if ($lastJob.status -notin @("completed", "failed")) {
    throw "Timed out waiting for job $jobId. Last status: $($lastJob.status)"
}

Write-Host ""
Write-Host "Job result"
Write-Host "job_id: $($lastJob.job_id)"
Write-Host "status: $($lastJob.status)"
Write-Host "document_number: $($lastJob.document_number)"
Write-Host "order_document_number: $($lastJob.order_document_number)"
Write-Host "output_blob_name: $($lastJob.output_blob_name)"

if ($lastJob.billing_summary) {
    $billing = $lastJob.billing_summary
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
