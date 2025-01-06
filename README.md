# agenda-api

## requirements
- python 3.11
- poetry
- gcloud
- docker desktop

## initial setup
1. gcloud login and configure docker
```
gcloud auth login
gcloud auth configure-docker
```

2. set environment variables
```
export PROJECT_ID={PROJECT_ID}
export ARTIFACT_REGISTRY={ARTIFACT_REGISTRY}
```

3. install dependencies
```
poetry install
```

## local test
```
poetry run uvicorn app.main:app --reload
```


## deploy
1. docker image building and push to artifact registry

```
docker build -t facilitator-agent .
docker tag facilitator-agent gcr.io/{PROJECT_ID}/{ARTIFACT_REGISTRY}/facilitator-agent
docker push gcr.io/{PROJECT_ID}/{ARTIFACT_REGISTRY}/facilitator-agent
```

2. deploy to cloud run

```
gcloud run deploy {SERVICE_NAME} --image gcr.io/{PROJECT_ID}/{ARTIFACT_REGISTRY}/facilitator-agent \
--platform managed \
--region asia-northeast1 \
--no-allow-unauthenticated \
```
