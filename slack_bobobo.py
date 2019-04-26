#coding:utf-8

import boto3
import json
import os
import logging
import decimal
import requests
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key
from datetime import datetime, date, timedelta

#ロギング
logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource('dynamodb',region_name = 'ap-northeast-1')
trgt_table = dynamodb.Table(os.getenv('Dynamo_Table'))

#SlackApiにイベントの種類に応じてGetを投げる
def get_slack_api(base_url,token,file_id):

    headers = {'Authorization': 'Bearer {}'.format(token)}
    
    response = requests.get(base_url+'files.info'+'?file='+file_id,headers=headers)

    return json.loads(response.text)

#画像ダウンロード
def get_image(image_url,token):
    headers = {'Authorization': 'Bearer {}'.format(token)}
    
    image = requests.get(image_url,headers=headers)

    return image.content

#S3にアップロード
def image_put_s3(image,key,mimetype,bucket_name):
    s3 = boto3.resource('s3',region_name = 'ap-northeast-1').Bucket(bucket_name)
    s3_obj = s3.Object(key)
    
    s3_obj.put(
    Body=image,
    StorageClass='STANDARD',
    ContentType=mimetype
    )

    return 

#Slackに投稿
def Slack_post(webhook_url,label,text,key):
    message ='S3に'+key+'をアップロードしました。\n'
    message +='解析結果\n'
    message +='\n物体検出\n'

    #物体検出
    for lb_n in label:
        message +=lb_n['Name']+':'+str(round(float(lb_n['Confidence']),1))+'%\n'

    message +='\nテキスト検出\n'

    #テキスト検出
    for tex_n in text:
        message +=tex_n['DetectedText']+':'+str(round(float(tex_n['Confidence']),1))+'%\n'

    item= { 'text':  message }
    
    headers = {'Content-type': 'application/json'}
    
    try:
      requests.post(webhook_url,json=item,headers=headers)
      
    except Exception as e:
      logging.info("type:%s", type(e))
      logging.error(e)

    return

#rekogniton解析したやつをDynamoに投げる
def rekogniton_image(bucket_name,key):
    rekogniton = boto3.client('rekognition',region_name ='ap-northeast-1')

    #boto3の公式リファレンス
    #https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/rekognition.html
    reko_label = rekogniton.detect_labels(Image={'S3Object':{'Bucket':bucket_name,'Name':key}},MinConfidence=75)['Labels']
    reko_text = rekogniton.detect_text(Image={'S3Object':{'Bucket':bucket_name,'Name':key}})['TextDetections']

    #DynamoDBはFloat型をサポートしていない為、Decimal型に変換する必要がある
    item={
        'Imageid':key,
        'timestamp':int(datetime.utcnow().timestamp()),
        'label':json.loads(json.dumps(reko_label),parse_float=decimal.Decimal),
        'text':json.loads(json.dumps(reko_text),parse_float=decimal.Decimal)
        }

    res = trgt_table.put_item(Item=item)

    if(res):
        Slack_post(os.getenv('Webhook_url'),item['label'],item['text'],key)
    
    return     


def lambda_handler(event,context):
    #イベント設定用
    body = json.loads(event['body'])
    if "challenge" in body:
      response = {
      'statusCode':'200',
      'body':body,
      'headers':{'Content-Type':'application/json'}
      }
      return response
    else:
        try:
          #DynamoDB存在判定
          res_get_item = trgt_table.get_item(Key={'Imageid':body['event']['file_id']})

          if 'Item' not in res_get_item:
              file_res = get_slack_api(os.getenv('Slack_Api_Url'),os.getenv('Oauth_Token'),body['event']['file_id'])
          else:
            raise Exception("Already Exist Same Imageid!")

        except Exception as e:
          logging.info("type:%s", type(e))
          logging.error(e)
            
        else:
          try:
            image = get_image(file_res['file']['url_private'],os.getenv('Oauth_Token'))

          except Exception as e:
            logging.info("type:%s", type(e))
            logging.error(e)
          else:
            try:
              s3_key = file_res['file']['id'] + '.' + file_res['file']['mimetype'].split('/')[1]
              image_put_s3(image,s3_key,file_res['file']['mimetype'],os.getenv('Bucket_Name'))

            except ClientError as e:
              logging.info(e.response['Error']['Message'])
              logging.error(e)

            else:
              try:
                rekogniton_image(os.getenv('Bucket_Name'),s3_key)

              except ClientError as e:
                logging.info(e.response['Error']['Message'])
                logging.error(e)

              except Exception as e:
                logging.info("type:%s", type(e))
                logging.error(e)

              else:
                response = {
                'statusCode':'200',
                'body':'OK',
                'headers':{'Content-Type':'application/json'}
                }

                return response
