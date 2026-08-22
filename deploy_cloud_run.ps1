# Google Cloud Run Deployment Script for Greenlight Studio
# Region: us-central1 (default) or asia-northeast1

param (
    [string]$ProjectId = "curricushift-ai-249973",
    [string]$Region = "us-central1",
    [string]$ServiceName = "greenlight-studio"
)

Write-Host "🎬 Deploying Greenlight Studio to Google Cloud Run..." -ForegroundColor Cyan
Write-Host "Project: $ProjectId"
Write-Host "Region: $Region"
Write-Host "Service: $ServiceName"

# Configure project
gcloud config set project $ProjectId

# Submit build to Cloud Build and Deploy directly to Cloud Run
gcloud run deploy $ServiceName `
    --source . `
    --platform managed `
    --region $Region `
    --allow-unauthenticated `
    --memory 1Gi `
    --cpu 1 `
    --min-instances 0 `
    --max-instances 3

Write-Host "🚀 Deployment command finished!" -ForegroundColor Green
