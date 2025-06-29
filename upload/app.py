from utils import responder, tryCatchHandler, bodyParser, bodyValidator, getChunksInfo, selectAndModifyChunk, s3Upload, appendToChunkFile, s3Delete
import json, base64
from decimal import Decimal
from config import HTTP
from typing import Dict, Any

@tryCatchHandler
def lambda_handler(evt: Dict[str, Any], _ctx: Any) -> Dict[str, Any]:
    # Decode the base64-encoded request body
    try:
        decodedBody = base64.b64decode(evt.get("body", b""))
    except Exception:
        return responder(HTTP.BAD_REQUEST, {
            "error": "invalid multipart request"
        })
    
    # validate and parse request body
    if not decodedBody:
        return responder(HTTP.BAD_REQUEST, {
            "error": "invalid multipart request"
        })
    
    parsedBodyResult = bodyParser(requestBody=decodedBody, requestHeader=evt.get("headers", {}))
    if "error" in parsedBodyResult:
        return responder(HTTP.BAD_REQUEST, {
            "error": "invalid multipart request"
        })

    validatorResult = bodyValidator(req_body=parsedBodyResult)
    if validatorResult is not True:
        return validatorResult

    requestFile = parsedBodyResult.get("img")
    labelValue = parsedBodyResult.get("label").get("value")
    weightValue = parsedBodyResult.get("weight", {}).get("value")

    # make/attempt-to upload to S3
    s3Key = s3Upload(
        fileDict=requestFile,
        label=labelValue,
    )

    if not s3Key:
        return responder(HTTP.INTERNAL_ERROR, {
                "error": "Failure to upload the image, please try again."
            })

    try:
        # fetch and confirm chunk info from dynamoDB
        chunksInfoJson = getChunksInfo(label=labelValue)
        selectedChunkID = selectAndModifyChunk(chunkMetadata=chunksInfoJson, label=labelValue)

        requestWeight = Decimal(str(weightValue)) if weightValue is not None else Decimal("0.5")
        appendToChunkFile(                     
            label=labelValue,
            chunkId=selectedChunkID,
            newEntry={
                "s3key": s3Key,
                "weight": requestWeight
            }
        )

    except Exception as _e:
        # metadata insertion failed, delete uploaded image from S3
        s3Delete(s3Key=s3Key)
        return responder(HTTP.INTERNAL_ERROR, {
                "error": "Failure to upload the image, please try again."
            })

    return {
        "statusCode": HTTP.OK,
        "body": json.dumps({"message": "Image uploaded successfully"})
    }
