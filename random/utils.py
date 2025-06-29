from typing import Any, Callable, Dict, List, Union
from boto3.dynamodb.conditions import Key
from botoClients import dynamodb, s3
from decimal import Decimal
import json, random, os
import config as cfg

def responder(statusCode: int, bodyMessage: Dict[str, Any]) -> Dict[str, Any]:
    """
    Constructs a standardized HTTP response for API Gateway.

    This function wraps a given status code and response body into the format 
    expected by AWS Lambda proxy integrations.

    Args:
        statusCode (int): The HTTP status code to return (e.g., 200, 400, 500).
        bodyMessage (Dict[str, Any]): A dictionary representing the response payload.

    Returns:
        Dict[str, Any]: A dictionary with `statusCode`, `headers`, and a JSON-encoded `body`.
    """
    return {
        "statusCode": statusCode,
        "headers": {
            "Content-Type": "application/json"
        },
        "body": json.dumps(bodyMessage)
    }

def tryCatchHandler(func: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
    """
    A decorator to wrap Lambda handler functions with standardized exception handling.

    This decorator ensures that:
      - Known client-side errors (e.g., invalid input) are caught as `ValueError`
        and responded with a 400 Bad Request and a sanitized error message.
      - Unhandled server-side exceptions are logged and responded with a generic
        500 Internal Server Error message without exposing stack traces to the client.

    Logs:
      - Client errors as warnings
      - Server errors as full stack traces for diagnostics

    Args:
        func (Callable[..., Dict[str, Any]]): The Lambda function to wrap.

    Returns:
        Callable[..., Dict[str, Any]]: A new function that wraps `func` with error handling logic.
    """
    def wrapper(*args, **kwargs) -> Dict[str, Any]:
        try:
            return func(*args, **kwargs)
        except ValueError as ve:
            # client-side errors
            cfg.logger.warning(f"ValueError: {ve}")
            return responder(400, {"error": "Bad request"})
        except Exception as ge:
            # internal error 
            cfg.logger.error(f"Unhandled Exception: {ge}", exc_info=True)
            return responder(500, {"error": "Internal server error"})
    return wrapper

def validateQueryParam(qp: dict[str, str]) -> bool:
    """
    Validates that the query parameters dictionary contains exactly one parameter:
    a 'label' key whose value is one of the allowed labels defined in config.

    Args:
        qp (dict[str, str]): The query parameters dictionary from the HTTP request.

    Returns:
        bool: True if the query parameters are valid (only 'label' key and allowed value),
              False otherwise.
    """
    return (
        bool(qp) and
        len(qp) == 1 and
        qp.get("label") in cfg.ALLOWED_LABELS
    )
    
def getRandomImageChunk(label: str) -> List[Dict[str, Union[str, Decimal]]]:
    """
    Retrieves a random chunk of image metadata for a given label from DynamoDB.

    This function:
        - Fetches the total number of chunks for the specified label.
        - Randomly selects one chunk index within the valid range.
        - Queries the image chunk table to return all image entries from that chunk.

    Args:
        label (str): The label/category of the images (e.g., "cat", "dog").

    Returns:
        List[Dict[str, Union[str, Decimal]]]: A list of image metadata dictionaries,
            each containing 's3key' and 'weight'. Returns an empty list if no chunks exist.

    Raises:
        RuntimeError: If any DynamoDB operation fails (fetching metadata or querying images).
    """
    # table setups
    infoTableName = cfg.getSSMParam(paramName="/pet-api/production/dynamoDB/chunk-info-table-name", default=os.environ.get("fb_chunkInfoTableName"))
    infoTable = dynamodb.Table(infoTableName)
    chunkTableName = cfg.getSSMParam(paramName="/pet-api/production/dynamoDB/image-chunks-table-name", default=os.environ.get("fb_chunkImageTableName"))
    chunkTable = dynamodb.Table(chunkTableName)

    # get chunk metadata within a given label
    try:
        responseMetadata = infoTable.get_item(Key={"label": label})["Item"]
        chunksNumber = int(responseMetadata["chunksNumber"])
    except Exception as _e:
        raise RuntimeError("Failed to retrieve chunk metadata from DynamoDB")

    # edge case: no image/chunk exists
    if chunksNumber == 0:
        return []
    
    # take a random chunk index in [0, chunksNumber) 
    randomChunkIDX = random.randint(0, chunksNumber - 1)

    # get images within a given random chunk
    try:
        response = chunkTable.query(
            IndexName="LabelChunkIndex",
            KeyConditionExpression=Key("label").eq(label) & Key("chunkNumber").eq(Decimal(randomChunkIDX)),
            ProjectionExpression="s3key, weight"
        )
        return response.get("Items", [])
    except Exception as _e:
        raise RuntimeError("Failed to retrieve images from image-chunks table")

def weightedRandomChoice(imageList: list[dict]) -> str:
    """
    Selects an image's S3 key from a list of image metadata using weighted random selection.

    Each image in the list must have a 'weight' value. The probability of each image being 
    selected is proportional to its weight.

    Args:
        imageList (list[dict]): A list of dictionaries, each containing at least:
            - 's3key' (str): The S3 key for the image.
            - 'weight' (Decimal): The selection weight.

    Returns:
        str: The 's3key' of the selected image.
    """
    totalWeight = sum(Decimal(item["weight"]) for item in imageList)
    rand = totalWeight * Decimal(str(random.random()))
    
    for item in imageList:
        rand -= item["weight"]
        if rand < 0:
            return item["s3key"]
    # fallback
    return imageList[-1]["s3key"] 

def getImageFromS3(bucketName: str, bucketKey: str) -> bytes:
    """
    Retrieves binary image data from an S3 bucket using the provided bucket name and key.

    Args:
        bucketName (str): The name of the S3 bucket.
        bucketKey (str): The key (path) to the image object in the bucket.

    Returns:
        bytes: The binary content of the image.
    """
    return s3.get_object(
        Bucket=bucketName,
        Key=bucketKey
    )["Body"].read()


def extractBucketInfo(s3Key: str) -> tuple[str, str]:
    """
    Extracts the bucket name and object key from a full S3 URL.

    Assumes the format of the S3 URL is:
    https://<bucket-name>.s3.amazonaws.com/<object-key>

    Args:
        s3Key (str): The full S3 URL.

    Returns:
        tuple[str, str]: A tuple containing:
            - bucketName (str): The name of the S3 bucket.
            - bucketKey (str): The path/key of the object in the bucket.
    """
    # remove "https://" prefix
    removedHttpsPrefix = s3Key[8:]  
    
    bucketName, bucketKey = removedHttpsPrefix.split(".s3.amazonaws.com/")
    return (bucketName, bucketKey)


def extractContentType(key: str) -> str:
    """
    Determines the MIME content type based on the file extension in the S3 object key.

    Args:
        key (str): The key or filename (e.g., 'cat/image_123.jpg').

    Returns:
        str: The appropriate MIME type string for HTTP response headers.
    """
    if key.endswith(".jpg") or key.endswith(".jpeg"):
        return "image/jpeg"
    elif key.endswith(".png"):
        return "image/png"
    elif key.endswith(".webp"):
        return "image/webp"
    else: # fallback
        return "application/octet-stream"  