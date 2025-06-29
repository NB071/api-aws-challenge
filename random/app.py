from utils import tryCatchHandler, responder, validateQueryParam, getRandomImageChunk, weightedRandomChoice, getImageFromS3, extractContentType, extractBucketInfo
from config import ALLOWED_LABELS, HTTP
from typing import Any, Dict
import base64

@tryCatchHandler
def lambda_handler(evt: Dict[str, Any], _ctx: Any) -> Dict[str, Any]:
    # query validation
    query_params = evt.get("queryStringParameters", {})
    
    if not validateQueryParam(qp=query_params):
        return responder(statusCode=HTTP.BAD_REQUEST, bodyMessage={"error": f"invalid query parameter. supported parameters: 'label' ({sorted(ALLOWED_LABELS)})"})
    
    label = query_params["label"]

    # get images within a random chunk from DynamoDB
    chunkImages = getRandomImageChunk(label=label)
    if not chunkImages:
        return responder(statusCode=HTTP.NOT_FOUND, bodyMessage={"error": f"no images found for the label = '{label}'"})
    
    # weighted selection based on the chunk 
    selectedUrl = weightedRandomChoice(imageList=chunkImages)
    
    # image retrival from S3
    (bucketName, bucketKey) = extractBucketInfo(s3Key=selectedUrl)
    imageBytes = getImageFromS3(bucketName=bucketName, bucketKey=bucketKey)


    return {
        "statusCode": HTTP.OK,
        "isBase64Encoded": True,
        "headers": {
            "Content-Type": extractContentType(key=bucketKey)
        },
        "body": base64.b64encode(imageBytes).decode("utf-8")  
    }