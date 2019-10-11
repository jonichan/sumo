import json
import requests
import base64
import logging
import pytz
import datetime as dt
import sys
import numpy as np
from datetime import datetime
from pytz import timezone

from google.cloud import bigquery
bq_client = bigquery.Client()

dataset_name = 'sumo'
dataset_ref = bq_client.dataset(dataset_name)

logger = logging.getLogger(__name__)


def get_max_submission_date():
  qry_max_date = ("""SELECT max(Date_Submitted) max_date FROM {0} """).format(dataset_name + ".surveygizmo")
  query_job = bq_client.query(qry_max_date)
  
  # TODO: should put this in try-catch and return default datetime
  max_date_result = query_job.to_dataframe() 
  max_date = max_date_result['max_date'].values[0] # format 2019-07-13 23:49:22 UTC

  # convert from numpy datetime64 to datetime UTC to EST/EDT
  ts = (max_date - np.datetime64('1970-01-01T00:00:00Z')) / np.timedelta64(1, 's')
  datetime_obj_utc = datetime.utcfromtimestamp(ts)
  datetime_obj_et = datetime_obj_utc.astimezone(timezone('US/Eastern'))
  
  return datetime_obj_et


def update_bq_table(uri, dataset_name, table_name):

  dataset_ref = bq_client.dataset(dataset_name)
  table_ref = dataset_ref.table(table_name)
  job_config = bigquery.LoadJobConfig()
  job_config.write_disposition = "WRITE_APPEND"
  job_config.source_format = bigquery.SourceFormat.CSV
  job_config.skip_leading_rows = 1
  job_config.autodetect = True

  load_job = bq_client.load_table_from_uri(uri, table_ref, job_config=job_config)  # API request
  print("Starting job {}".format(load_job.job_id))

  load_job.result()  # Waits for table load to complete.
  destination_table = bq_client.get_table(table_ref)
  print("Loaded {} rows.".format(destination_table.num_rows))


def convert_to_utc(dt_str):
  fmt = '%Y-%m-%d %H:%M:%S'
  ds, tzs = dt_str.rsplit(' ', 1)
  eastern=pytz.timezone('US/Eastern')
  try:
    dt_utc = dt.datetime.strptime(ds, fmt)
    if tzs == 'EST':
      date = dt.datetime.strptime(ds, fmt)
      date_eastern=eastern.localize(date,is_dst=False)
      date_utc=date_eastern.astimezone(pytz.utc)
    elif tzs == 'EDT':
      date = dt.datetime.strptime(ds, fmt)
      date_eastern=eastern.localize(date,is_dst=True)
      date_utc=date_eastern.astimezone(pytz.utc)
    return dt_utc
  except ValueError:
    logging.info("ValueError:" + dt_str)
    raise


def get_answer(survey_data_row, question_num, default):
    try:
        return survey_data_row[question_num]['answer']
    except:
        return default
        
def get_survey_data_row(row):
    try:
      dt_started = convert_to_utc(row['date_started'])
      dt_submitted = convert_to_utc(row['date_submitted'])
      return [row['id'], dt_started, dt_submitted, row['status'],
              row['contact_id'], row['language'],
              row['referer'], row['session_id'], row['user_agent'],
              row['longitude'],
              row['latitude'], row['country'], row['city'], row['region'], row['postal'],
              get_answer(row['survey_data'], str(2), ''),
              get_answer(row['survey_data'], str(4), '')]
    except ValueError:
      logging.info("empty get_survey_data_row")

def get_survey_data(api_url_base, params):
	api_url = '{0}?_method=GET'.format(api_url_base)
	results = []
	
	max_submission_dt = get_max_submission_date().strftime('%Y-%m-%d+%H:%M:%S')
	print(max_submission_dt)
	# add filter for submission times >= max submission date in EST/EDT
	
	# add to params
	params.update( {'filter[field][0]' : 'date_submitted', 'filter[operator][0]' : '>', 'filter[value][0]' : max_submission_dt} )

	payload_str = "&".join("%s=%s" % (k,v) for k,v in params.items())
	response = requests.get(api_url, params=payload_str)
	#print(response.request.url)

	# need to get total_pages value and loop through to get all data &page=#
	fields = ["Response ID","Time Started","Date Submitted","Status",
			  "Contact ID","Language", #"Legacy Comments","Comments",
			  "Referer","SessionID","User Agent", #"Extended Referer",
			  "Longitude", #
			  "Latitude","Country","City","State/Region","Postal",
			  "Did you accomplish the goal of your visit?", #2
			  "How would you rate your experience with support.mozilla.org (Please help us by only rating the website and not Firefox)", #4
			  ]

	results.append(fields)
			   
	if response.status_code == 200:
		raw = None
		try:
			raw = response.json()
		except ValueError:
			logger.error("Content isn't valid JSON : %r" % response.content)
			raise

		for i in raw['data']:
			results.append(get_survey_data_row(i))
			
		total_pages = raw['total_pages']
		print("Total Pages: {}".format(total_pages))
		print("Total Count: {}".format(raw['total_count']))

		for page in range(2, total_pages):
			params['page'] = str(page)			
			#print(page)
			
			try:
				response = requests.get(api_url, params=params)
			except Exception as e:
				print(e)
				print(sys.exc_info()[0])
				continue
			
			if response.status_code == 200:
				
				logger.info('status code 200')
				
				raw = response.json()
				for i in raw['data']:
				    data_row =get_survey_data_row(i)
				    if data_row:
				      results.append(data_row)

			else:
				print('[!] HTTP {0} calling [{1}]'.format(response.status_code, api_url)) # 401 unauthorized

		logger.info('returning {} results'.format(len(results)))
		return results

	else:
		print('[!] HTTP {0} calling [{1}]'.format(response.status_code, api_url)) # 401 unauthorized
		return None

