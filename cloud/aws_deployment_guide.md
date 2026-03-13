### 🚀 Automated Deployment (GitHub Actions)

The pipeline is now fully automated via GitHub Actions. Deployment happens automatically whenever you push to the `main` branch.

#### 1. Required GitHub Secrets
Ensure the following secrets are added to your GitHub repository (`Settings -> Secrets and variables -> Actions`):
- `AWS_ACCESS_KEY_ID`: Your IAM user Access Key.
- `AWS_SECRET_ACCESS_KEY`: Your IAM user Secret Key.
- `AWS_REGION`: `us-east-2`
- `AWS_ACCOUNT_ID`: `135100771046`
- `GEMINI_API_KEY`: Your Gemini key.
- `VERTEX_API_KEY`: Your Vertex/GCP key.
- `GOOGLE_CLOUD_PROJECT`: `youtube-video-creation-488019`
- `GOOGLE_CLOUD_LOCATION`: `us-central1`

#### 2. How to Trigger
Simply commit and push your changes:
```cmd
git add .
git commit -m "Your update message"
git push origin main
```

---

### 🌐 Fetching Data for Other Projects

The S3 bucket is now configured to allow Cross-Origin Resource Sharing (CORS) and Public Read Access. 

**New Fetch URL (Ohio):**
`https://uscf-chess-data-135100771046.s3.us-east-2.amazonaws.com/uscf_tournaments_refined.json`

---

### ⏰ How to set up a Daily Trigger
To make the scraper run automatically every day at 12:00 UTC, run these three commands (the script ensures `%REGION%` is `us-east-2`):

1. **Create the Schedule Rule**:
   ```cmd
   aws events put-rule --name USCF-Daily-Crawl --schedule-expression "cron(0 12 * * ? *)" --region us-east-2
   ```
2. **Give EventBridge Permission to call Lambda**:
   ```cmd
   aws lambda add-permission --function-name USCF-Crawler --statement-id EventBridgeTrigger --action "lambda:InvokeFunction" --principal events.amazonaws.com --source-arn arn:aws:events:us-east-2:%ACCOUNT_ID%:rule/USCF-Daily-Crawl --region us-east-2
   ```
3. **Add the Target**:
   ```cmd
   aws events put-targets --rule USCF-Daily-Crawl --targets "Id"="1","Arn"="arn:aws:lambda:us-east-2:%ACCOUNT_ID%:function:USCF-Crawler" --region us-east-2
   ```

---

### 🛡️ How Delta Scraping works in the Cloud
Is it gathering 900 results every day? **No!**
I have implemented **Cloud Delta Scraping** directly into the `USCF-Crawler`:
*   Before the Crawler sends a URL to SQS, it checks the **`uscf_tournaments_refined.json`** file in your S3 bucket.
*   If a tournament is already in that file, it **skips it**.
*   **Result**: Only brand-new tournaments will trigger the AI Refiner, saving you AI credits.

The script performs the following 7 stages:
1.  🏗️ **Setup**: Detects your Account ID and enforces `us-east-2`.
2.  📦 **Infrastructure**: Creates the S3 bucket, SQS queue, applies **CORS**, and sets **Public Read Policy**.
3.  🐳 **ECR**: Builds your Docker image and pushes it to AWS.
4.  🛡️ **IAM**: Creates a role with the necessary SQS and S3 permissions.
5.  🛰️ **Crawler**: Deploys the Playwright-based crawler as a Docker Lambda.
6.  🧪 **Refiner**: Zips your code and deploys the AI Refiner Lambda.
7.  🔗 **Connect**: Sets up the SQS trigger so the Refiner fires automatically.
