from fastapi import FastAPI
from fastapi.responses import PlainTextResponse


app = FastAPI(title='gmail-temp-mail')


@app.get('/', response_class=PlainTextResponse)
def root() -> str:
    return 'OK'


@app.get('/health_check', response_class=PlainTextResponse)
def health_check() -> str:
    return 'OK'
