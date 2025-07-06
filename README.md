# 🐾 petAPI - Weighted Random Image Service
I want to start by expressing my gratitude to *Jitto* for giving me the chance to work on this technical challenge. The process of creating this application has been full of interesting discoveries as well as difficulties. It forced me to dive deeper into AWS services, optimize for scale, and make deliberate architectural choices while dealing with practical limitations.

---

## 🚀 Overview
This application allows users to upload images (e.g., of pets) along with labels and weights, and later retrieve a randomly selected image, along with selection probability **weighted by user-defined values**. It simulates a simplified content recommendation or image rotation service.


## ☁️ AWS Services Used

<table>
  <tr>
    <td align="center">
      <img src="https://icon.icepanel.io/AWS/svg/Security-Identity-Compliance/Identity-and-Access-Management.svg" width="64"/><br/>
      <strong>IAM</strong>
    </td>
    <td align="center">
      <img src="https://icon.icepanel.io/AWS/svg/Compute/Lambda.svg" width="64"/><br/>
      <strong>Lambda</strong>
    </td>
    <td align="center">
      <img src="https://icon.icepanel.io/AWS/svg/Storage/Simple-Storage-Service.svg" width="64"/><br/>
      <strong>S3</strong>
    </td>
    <td align="center">
      <img src="https://icon.icepanel.io/AWS/svg/App-Integration/API-Gateway.svg" width="64"/><br/>
      <strong>API Gateway</strong>
    </td>
  </tr>
  <tr>
    <td align="center">
      <img src="https://icon.icepanel.io/AWS/svg/Management-Governance/Systems-Manager.svg" width="64"/><br/>
      <strong>SSM</strong>
    </td>
    <td align="center">
      <img src="https://icon.icepanel.io/AWS/svg/Database/DynamoDB.svg" width="64"/><br/>
      <strong>DynamoDB</strong>
    </td>
    <td align="center">
      <img src="https://icon.icepanel.io/AWS/svg/Management-Governance/CloudWatch.svg" width="64"/><br/>
      <strong>CloudWatch</strong>
    </td>
    <td align="center">
      <img src="https://icon.icepanel.io/AWS/svg/Management-Governance/CloudFormation.svg" width="64"/><br/>
      <strong>CloudFormation</strong>
    </td>
  </tr>
</table>

---

## 🛠 Local Development & Deployment with AWS SAM

This project uses the **AWS Serverless Application Model (SAM)** for local development, testing, and deployment.

### 🔄 Local Testing

```bash
sudo sam build && sam local start-api
```
Starts a local API Gateway at `http://127.0.0.1:3000` to invoke your Lambda functions locally.

### 🚀 Deploy to AWS
```bash
sudo sam build && sam deploy
```
Builds and deploys the application to AWS. Make sure your AWS credentials are configured (`aws configure`) beforehand.

### 🧩 IAM Role Configuration

Each Lambda function directory should contain an IAM policy file named: `IAM__policy.json`
You must modify `template.yaml` to reference the appropriate IAM role for each Lambda function by either:

- Attaching the role directly via the Role property
- Or using Policies: to inline attach `IAM__policy.json`

`NOTE:` For security and privacy reasons, the `template.yaml` file has been anonymized. If you require access to the original version for review or deployment purposes, feel free to reach out to me directly.

### 📦 S3 & DynamoDB Setup

Make sure the following AWS resources exist before deploying the stack:

- **AWS SSM Parameter Store for Config Management**
  * Environment-dependent values such as the S3 bucket name and DynamoDB table names are stored in AWS Systems Manager Parameter Store under following paths:
    * `/pet-api/production/s3/upload-bucket-name`
    * `/pet-api/production/dynamoDB/chunk-info-table-name`
    * `/pet-api/production/dynamoDB/image-chunks-table-name`
- **S3 Bucket *(for image uploads/retrival)***
  * for starter, 2 directories for each category (cat/dog)
  * Set in environment variable: `fb_S3BucketName`

- **DynamoDB Tables**
  * `chunkInfoTable` — stores chunk metadata per label
    * PartitionKey = `label` 
    * Fields = {`label`:str, `activeChunks`:List, `chunkMax`:Number, `chunkNumber`:Number, `chunkThreshold`:Number, `chunkVolume`:List}
  * `imageChunksTable`
    * PartitionKey = `label` | sortKey = `s3key`
    * Fields = {`label`:str, `s3key`:str, `chunkNumber`:Number, `weight`:Number}
    * Must include a `GSI`: **LabelChunkIndex**(partition key: `label`, sort key: `chunkNumber`)
  * Set fallback tables via environment variables :
    * `fb_chunkInfoTableName`
    * `fb_chunkImageTableName`

---

## API Endpoints

### 📤 Upload Image

**POST**  
`https://blf1qj5mpl.execute-api.ca-central-1.amazonaws.com/Prod/upload`

- Accepts: `multipart/form-data`
- Fields:
  - `label`: (`cat` | `dog`) – required
  - `img`: image file (.jpg, .png, or .webp) – required
  - `weight`: integer, 0 < *weight* < 1 *(optional, default = 0.5)*

#### Example CURL command:
```bash
curl -X POST https://blf1qj5mpl.execute-api.ca-central-1.amazonaws.com/Prod/upload \
  -F "label=cat" \
  -F "img=@/path/to/your/image.jpg" \
  -F "weight=0.54321"
```


### 🎲 Get Random Image

**GET**  
`https://blf1qj5mpl.execute-api.ca-central-1.amazonaws.com/Prod/random?label={label}`

- Query Params:
  - `label`: (`cat` | `dog`) – required
- Response:
  - Base64-encoded image
  - Headers include correct MIME type (`Content-Type: image/jpeg` etc.)

#### Example CURL command:
```bash
curl "https://blf1qj5mpl.execute-api.ca-central-1.amazonaws.com/Prod/random?label=cat"
```

---

## 🧠 Design Highlights & Interesting Choices
* **Chunk-Based Image Management**: Images are grouped into *chunks per label*. Each chunk tracks the number of entries. This will help to:
  
  * Minimize read volume per request
  * Balance chunk sizes for efficient querying
  * Easily manage deletions in future versions (chunk threshold enforcement)
  * Less burden on actual algorithm responsible for sampling, bounding it to a constant/finite to complexity O(1)

* **Weighted Random Selection**: Instead of retrieving all images per label, the system will:

  * Randomly selects a chunk
  * Performs weighted random selection on only that chunk
This greatly reduces *read costs and improves latency*.

* **Optimized Lambda + S3 + DynamoDB Workflow**:
  * Upload requests go through API Gateway → Lambda → S3 & *DynamoDB*
  * Retrieval uses S3 for objects and DynamoDB for metadata, chunk tracking, and weights
  * Optimized SSM/ParameterStore access by **caching parameters** at runtime to reduce cold-start latency and avoid redundant network calls.

* **Error Handling & Logging**: 
  * Decorators wrap all Lambda handlers with unified **try/except** logic (previously integrated into my `gexEx` project)
  * No raw stack traces are exposed to clients — logs are routed to CloudWatch for internal review

* **Security & Access Control**: 
  * **Rate Limiting**: Added throttling using API Gateway settings to limit abuse or accidental spikes (Rate=*20*; Burst=*10*)
  * **IAM Roles with *Least Privilege***: Lambda functions are scoped to only access the necessary actions and resources (e.g., dynamodb:GetItem, s3:PutObject, ssm:GetParameter).
  * **No Stack Trace Exposure**: All exceptions are sanitized before reaching the client. Logs are stored securely in CloudWatch for internal debugging.
  * **MIME Type Filtering**: Only valid image MIME types (.jpg, .png, .webp) are accepted. Fallback type validation is done via content sniffing.
  * **Safe Defaults**: Responses default to application/json, and binary data responses are safely base64 encoded.

---

# 💸 Approx. Cost Analysis
According to AWS specifications, an approximate costs-by-scale is placed under costs directory located in this repository

--- 

# 🧩 Primary Technical Challenges & Solutions
* **Setting up the development environment and choosing a scalable structure**: Initially, organizing the project for scalability and clean deployment was challenging. After evaluating options, I adopted AWS SAM for its native integration with Lambda, API Gateway, and ease of local development. This required extensive documentation reading but resulted in a robust and production-ready setup.

* **Multipart/Form-Data Parsing in Lambda**: AWS Lambda doesn’t natively parse multipart/form-data and given library retriction of this challenge, I decided to write a custom parser using byte-level splitting logic. This ensured:
  * File uploads worked reliably with various clients
  * MIME types could be validated manually (e.g., fallback to imghdr when contentType is missing)

* **SSM Parameter Store Lookup**: Table and bucket names were dynamically fetched and cached from AWS SSM to keep environment configuration flexible and efficient. Errors here were safely wrapped and logged.

* **Chunk Balancing Logic/Idea**: Preventing overfilled or underutilized chunks required logic to:
  * Prefer filling chunks under a soft threshold
  * Dynamically create new chunks when no eligible one existed
  * Prepare for future features like `/delete` without degrading performance
  * Reducing scanning time from `O(n)` -> `O(k)` where *k* is the number of undefilled chunks (i.e. `chunkVolume < chnukThreshold`) by preserving a `activeChunk` field with proper logic and managemment.

---

# ⚡ Typical Response Time

During development, I experimented with three architectural iterations, each with measurable impact on response latency:

### 📦 **1. Initial Approach: S3 + DynamoDB (No Chunking)**
- **Warm Start**: ~500–800 ms  
- **Cold Start**: ~1100–2000 ms  
- **Details**:  
  - All image objects stored in S3  
  - All metadata stored in a single flat DynamoDB table  
  - No chunking led to large scan volumes and growing latency at scale

---

### 🧩 **2. S3-Only with Chunking**
- **Warm Start**: ~500–900 ms  
- **Cold Start**: ~1500–2000 ms  
- **Details**:  
  - Chunking introduced to reduce read volume  
  - However, metadata and weights were moved to S3, increasing S3 I/O overhead  
  - Slower cold starts due to full file reads and lack of metadata indexing

---

### ⚖️ **3. Final Approach: S3 + DynamoDB with Chunking**
- **Warm Start**: ~300–500 ms  
- **Cold Start**: ~1100–1500 ms  
- **Details**:  
  - S3 retained for image storage  
  - Chunking logic + weight metadata stored in DynamoDB (2 tables --> Separation of Concerns)
  - Balanced cost-efficiency and latency, leveraging indexed queries and targeted data access/modification (`update_item` instead of `put_item` in dynamoDB)

---

# 📌 Next Steps
* Add `/delete` endpoint to remove images and rebalance chunks
* Elavate security and performance by choocing latest version of python and *external libraries*
* potential cases where external libraries would benefit the application:
  * Compress/Resize image sizes to lower image sizes (e.g. `Pillow`)
  * More robust body parser and request validator to improve security (e.g. `marshmallow` or `cerberus`)
  * Improved MIME Type Detection instead of outdated `imghdr` library (e.g. `python-magic`)
  * AWS SSM parameter store reads were cached using a custom strategy, however, external caching tools like `functools` or `Redis` (via ElastiCache) could increase flexibility and performance at scale.
* ***JWT-based* authentication** for private buckets or user-bound uploads

---

# Resourses I Used 

Below is a *sample list of resources* I used during the development of this application. AWS docs were always the first to visit.

- https://boto3.amazonaws.com/v1/documentation/api/latest/guide/dynamodb.html *(API ref)*
- https://boto3.amazonaws.com/v1/documentation/api/latest/guide/s3-examples.html *(API ref)*
- https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ssm.html *(API ref)*
- https://docs.aws.amazon.com/lambda/latest/dg/services-apigateway-tutorial.html *(API ref)*
- https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html *(API ref)*
- https://docs.aws.amazon.com/lambda/latest/dg/python-handler.html#python-handler-best-practices *(API ref)*
- https://www.youtube.com/watch?v=ETphJASzYes&ab_channel=TheCodingTrainv
- https://codestax.medium.com/aws-parameter-store-b523cb190e0c
- https://beabetterdev.com/2023/01/07/an-introduction-to-aws-parameter-store/
- https://dev.to/dvddpl/dynamodb-dynamic-method-to-insert-or-edit-an-item-5fnh
- https://github.com/aws/serverless-application-model/blob/master/docs/globals.rst
- https://github.com/aws/serverless-application-model/blob/master/versions/2016-10-31.md#api
- https://www.youtube.com/watch?v=mhdX4znMd2Q&ab_channel=JonathanDavies
- https://en.wikipedia.org/wiki/List_of_file_signatures
- https://sceweb.sce.uhcl.edu/abeysekera/itec3831/labs/FILE%20SIGNATURES%20TABLE.pdf
- https://developers.google.com/speed/webp/docs/riff_container#webp_file_header
- https://docs.python.org/3.12/library/imghdr.html
- https://github.com/myshenin/aws-lambda-multipart-parser/tree/master *(inspiration)*
- https://docs.aws.amazon.com/lambda/latest/dg/python-logging.html
