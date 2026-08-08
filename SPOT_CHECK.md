# DocPilot corpus spot-check

Manual review package for the `fastapi` corpus. Sampled from 1582 total chunks (441 contain a ```` ```python ```` block). 10 random + 5 random code-bearing chunks, drawn independently and de-duplicated.

For each chunk: check the full content below against the live page at its `source_url`, then tick the box.

## Random chunks

### Random chunks — 1 · chunk `3414`

- **id:** 3414
- **heading_path:** Concurrency and async / await { #concurrency-and-async-await } > `async` and `await` { #async-and-await } > Write your own async code { #write-your-own-async-code }
- **source_url:** [https://fastapi.tiangolo.com/async/](https://fastapi.tiangolo.com/async/)

```text
Starlette (and **FastAPI**) are based on [AnyIO](https://anyio.readthedocs.io/en/stable/), which makes it compatible with both Python's standard library [asyncio](https://docs.python.org/3/library/asyncio-task.html) and [Trio](https://trio.readthedocs.io/en/stable/).

In particular, you can directly use [AnyIO](https://anyio.readthedocs.io/en/stable/) for your advanced concurrency use cases that require more advanced patterns in your own code.

And even if you were not using FastAPI, you could also write your own async applications with [AnyIO](https://anyio.readthedocs.io/en/stable/) to be highly compatible and get its benefits (e.g. *structured concurrency*).

I created another library on top of AnyIO, as a thin layer on top, to improve a bit the type annotations and get better **autocompletion**, **inline errors**, etc. It also has a friendly introduction and tutorial to help you **understand** and write **your own async code**: [Asyncer](https://asyncer.tiangolo.com/). It would be particularly useful if you need to **combine async code with regular** (blocking/synchronous) code.
```

[x] verified against live page

### Random chunks — 2 · chunk `3653`

- **id:** 3653
- **heading_path:** FastAPI { #fastapi } > Example { #example } > Interactive API docs { #interactive-api-docs }
- **source_url:** [https://fastapi.tiangolo.com/](https://fastapi.tiangolo.com/)

```text
Now go to [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

You will see the automatic interactive API documentation (provided by [Swagger UI](https://github.com/swagger-api/swagger-ui)):

![Swagger UI](https://fastapi.tiangolo.com/img/index/index-01-swagger-ui-simple.png)
```

[x] verified against live page

### Random chunks — 3 · chunk `3568`

- **id:** 3568
- **heading_path:** Features { #features } > Pydantic features { #pydantic-features }
- **source_url:** [https://fastapi.tiangolo.com/features/](https://fastapi.tiangolo.com/features/)

```text
**FastAPI** is fully compatible with (and based on) [**Pydantic**](https://docs.pydantic.dev/). So, any additional Pydantic code you have, will also work.

Including external libraries also based on Pydantic, such as ORM s and ODM s for databases.

This also means that in many cases you can pass the same object you get from a request **directly to the database**, as everything is validated automatically.

The same applies the other way around, in many cases you can just pass the object you get from the database **directly to the client**.

With **FastAPI** you get all of **Pydantic**'s features (as FastAPI is based on Pydantic for all the data handling):

* **No brainfuck**:
    * No new schema definition micro-language to learn.
    * If you know Python types you know how to use Pydantic.
* Plays nicely with your ** IDE / linter /brain**:
    * Because pydantic data structures are just instances of classes you define; auto-completion, linting, mypy and your intuition should all work properly with your validated data.
* Validate **complex structures**:
    * Use of hierarchical Pydantic models, Python `typing`’s `List` and `Dict`, etc.
    * And validators allow complex data schemas to be clearly and easily defined, checked and documented as JSON Schema.
    * You can have deeply **nested JSON** objects and have them all validated and annotated.
* **Extensible**:
    * Pydantic allows custom data types to be defined or you can extend validation with methods on a model decorated with the validator decorator.
* 100% test coverage.
```

[x] verified against live page

### Random chunks — 4 · chunk `3189`

- **id:** 3189
- **heading_path:** Custom Response - HTML, Stream, File, others { #custom-response-html-stream-file-others } > Available responses { #available-responses } > `FileResponse` { #fileresponse }
- **source_url:** [https://fastapi.tiangolo.com/advanced/custom-response/](https://fastapi.tiangolo.com/advanced/custom-response/)

````text
Asynchronously streams a file as the response.

Takes a different set of arguments to instantiate than the other response types:

* `path` - The file path to the file to stream.
* `headers` - Any custom headers to include, as a dictionary.
* `media_type` - A string giving the media type. If unset, the filename or path will be used to infer a media type.
* `filename` - If set, this will be included in the response `Content-Disposition`.

File responses will include appropriate `Content-Length`, `Last-Modified` and `ETag` headers.

```python
from fastapi import FastAPI
from fastapi.responses import FileResponse

some_file_path = "large-video-file.mp4"
app = FastAPI()


@app.get("/")
async def main():
    return FileResponse(some_file_path)
```

You can also use the `response_class` parameter:

```python
from fastapi import FastAPI
from fastapi.responses import FileResponse

some_file_path = "large-video-file.mp4"
app = FastAPI()


@app.get("/", response_class=FileResponse)
async def main():
    return some_file_path
```

In this case, you can return the file path directly from your *path operation* function.
````

[x] verified against live page

### Random chunks — 5 · chunk `3791`

- **id:** 3791
- **heading_path:** Release Notes > 0.123.2 (2025-12-02) > Docs
- **source_url:** [https://fastapi.tiangolo.com/release-notes/](https://fastapi.tiangolo.com/release-notes/)

```text
* 📝 Add tip on how to install `pip` in case of `No module named pip` error in `virtual-environments.md`. PR [#14211](https://github.com/fastapi/fastapi/pull/14211) by [@zadevhub](https://github.com/zadevhub).
* 📝 Update Primary Key notes for the SQL databases tutorial to avoid confusion. PR [#14120](https://github.com/fastapi/fastapi/pull/14120) by [@FlaviusRaducu](https://github.com/FlaviusRaducu).
* 📝 Clarify estimation note in documentation. PR [#14070](https://github.com/fastapi/fastapi/pull/14070) by [@SaisakthiM](https://github.com/SaisakthiM).
```

[x] verified against live page

### Random chunks — 6 · chunk `4418`

- **id:** 4418
- **heading_path:** Header Parameters { #header-parameters } > Duplicate headers { #duplicate-headers }
- **source_url:** [https://fastapi.tiangolo.com/tutorial/header-params/](https://fastapi.tiangolo.com/tutorial/header-params/)

````text
It is possible to receive duplicate headers. That means, the same header with multiple values.

You can define those cases using a list in the type declaration.

You will receive all the values from the duplicate header as a Python `list`.

For example, to declare a header of `X-Token` that can appear more than once, you can write:

```python
from typing import Annotated

from fastapi import FastAPI, Header

app = FastAPI()


@app.get("/items/")
async def read_items(x_token: Annotated[list[str] | None, Header()] = None):
    return {"X-Token values": x_token}
```

If you communicate with that *path operation* sending two HTTP headers like:

```
X-Token: foo
X-Token: bar
```

The response would be like:

```JSON
{
    "X-Token values": [
        "bar",
        "foo"
    ]
}
```

Declare headers with `Header`, using the same common pattern as `Query`, `Path` and `Cookie`.

And don't worry about underscores in your variables, **FastAPI** will take care of converting them.
````

[x] verified against live page

### Random chunks — 7 · chunk `4587`

- **id:** 4587
- **heading_path:** OAuth2 with Password (and hashing), Bearer with JWT tokens { #oauth2-with-password-and-hashing-bearer-with-jwt-tokens } > Install `PyJWT` { #install-pyjwt }
- **source_url:** [https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/)

````text
We need to install `PyJWT` to generate and verify the JWT tokens in Python.

Make sure you create a [virtual environment](../../virtual-environments.md), activate it, and then install `pyjwt`:

```console
$ pip install pyjwt

---> 100%
```

/// note

If you are planning to use digital signature algorithms like RSA or ECDSA, you should install the cryptography library dependency `pyjwt[crypto]`.

You can read more about it in the [PyJWT Installation docs](https://pyjwt.readthedocs.io/en/latest/installation.html).

///
````

[x] verified against live page

### Random chunks — 8 · chunk `4434`

- **id:** 4434
- **heading_path:** Middleware { #middleware }
- **source_url:** [https://fastapi.tiangolo.com/tutorial/middleware/](https://fastapi.tiangolo.com/tutorial/middleware/)

```text
You can add middleware to **FastAPI** applications.

A "middleware" is a function that works with every **request** before it is processed by any specific *path operation*. And also with every **response** before returning it.

* It takes each **request** that comes to your application.
* It can then do something to that **request** or run any needed code.
* Then it passes the **request** to be processed by the rest of the application (by some *path operation*).
* It then takes the **response** generated by the application (by some *path operation*).
* It can do something to that **response** or run any needed code.
* Then it returns the **response**.

/// note | Technical Details

If you have dependencies with `yield`, the exit code will run *after* the middleware.

If there were any background tasks (covered in the [Background Tasks](background-tasks.md) section, you will see it later), they will run *after* all the middleware.

///
```

[x] verified against live page

### Random chunks — 9 · chunk `3369`

- **id:** 3369
- **heading_path:** WebSockets { #websockets } > Using `Depends` and others { #using-depends-and-others }
- **source_url:** [https://fastapi.tiangolo.com/advanced/websockets/](https://fastapi.tiangolo.com/advanced/websockets/)

````text
In WebSocket endpoints you can import from `fastapi` and use:

* `Depends`
* `Security`
* `Cookie`
* `Header`
* `Path`
* `Query`

They work the same way as for other FastAPI endpoints/*path operations*:

```python
from typing import Annotated

from fastapi import (
    Cookie,
    Depends,
    FastAPI,
    Query,
    WebSocket,
    WebSocketException,
    status,
)
from fastapi.responses import HTMLResponse

app = FastAPI()

html = """
<!DOCTYPE html>
<html>
    <head>
        <title>Chat</title>
    </head>
    <body>
        <h1>WebSocket Chat</h1>
        <form action="" onsubmit="sendMessage(event)">
            <label>Item ID: <input type="text" id="itemId" autocomplete="off" value="foo"/></label>
            <label>Token: <input type="text" id="token" autocomplete="off" value="some-key-token"/></label>
            <button onclick="connect(event)">Connect</button>
            <hr>
            <label>Message: <input type="text" id="messageText" autocomplete="off"/></label>
            <button>Send</button>
        </form>
        <ul id='messages'>
        </ul>
        <script>
        var ws = null;
            function connect(event) {
                var itemId = document.getElementById("itemId")
                var token = document.getElementById("token")
                ws = new WebSocket("ws://localhost:8000/items/" + itemId.value + "/ws?token=" + token.value);
                ws.onmessage = function(event) {
                    var messages = document.getElementById('messages')
                    var message = document.createElement('li')
                    var content = document.createTextNode(event.data)
                    message.appendChild(content)
                    messages.appendChild(message)
                };
                event.preventDefault()
            }
            function sendMessage(event) {
                var input = document.getElementById("messageText")
                ws.send(input.value)
                input.value = ''
                event.preventDefault()
            }
        </script>
    </body>
</html>
"""


@app.get("/")
async def get():
    return HTMLResponse(html)


async def get_cookie_or_token(
    websocket: WebSocket,
    session: Annotated[str | None, Cookie()] = None,
    token: Annotated[str | None, Query()] = None,
):
    if session is None and token is None:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION)
    return session or token


@app.websocket("/items/{item_id}/ws")
async def websocket_endpoint(
    *,
    websocket: WebSocket,
    item_id: str,
    q: int | None = None,
    cookie_or_token: Annotated[str, Depends(get_cookie_or_token)],
):
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(
            f"Session cookie or query token value is: {cookie_or_token}"
        )
        if q is not None:
            await websocket.send_text(f"Query parameter q is: {q}")
        await websocket.send_text(f"Message text was: {data}, for item ID: {item_id}")
```
````

[x] verified against live page

### Random chunks — 10 · chunk `4513`

- **id:** 4513
- **heading_path:** Request Files { #request-files } > `UploadFile` with Additional Metadata { #uploadfile-with-additional-metadata }
- **source_url:** [https://fastapi.tiangolo.com/tutorial/request-files/](https://fastapi.tiangolo.com/tutorial/request-files/)

````text
You can also use `File()` with `UploadFile`, for example, to set additional metadata:

```python
from typing import Annotated

from fastapi import FastAPI, File, UploadFile

app = FastAPI()


@app.post("/files/")
async def create_file(file: Annotated[bytes, File(description="A file read as bytes")]):
    return {"file_size": len(file)}


@app.post("/uploadfile/")
async def create_upload_file(
    file: Annotated[UploadFile, File(description="A file read as UploadFile")],
):
    return {"filename": file.filename}
```
````

[x] verified against live page

## Code-bearing chunks

### Code-bearing chunks — 1 · chunk `4371`

- **id:** 4371
- **heading_path:** First Steps { #first-steps }
- **source_url:** [https://fastapi.tiangolo.com/tutorial/first-steps/](https://fastapi.tiangolo.com/tutorial/first-steps/)

````text
The simplest FastAPI file could look like this:

```python
from fastapi import FastAPI

app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Hello World"}
```

Copy that to a file `main.py`.

Run the live server:

```console
$ <font color="#4E9A06">fastapi</font> dev

  <span style="background-color:#009485"><font color="#D3D7CF"> FastAPI </font></span>  Starting development server 🚀

             Searching for package file structure from directories
             with <font color="#3465A4">__init__.py</font> files
             Importing from <font color="#75507B">/home/user/code/</font><font color="#AD7FA8">awesomeapp</font>

   <span style="background-color:#007166"><font color="#D3D7CF"> module </font></span>  🐍 main.py

     <span style="background-color:#007166"><font color="#D3D7CF"> code </font></span>  Importing the FastAPI app object from the module with
             the following code:

             <u style="text-decoration-style:solid">from </u><u style="text-decoration-style:solid"><b>main</b></u><u style="text-decoration-style:solid"> import </u><u style="text-decoration-style:solid"><b>app</b></u>

      <span style="background-color:#007166"><font color="#D3D7CF"> app </font></span>  Using import string: <font color="#3465A4">main:app</font>

   <span style="background-color:#007166"><font color="#D3D7CF"> server </font></span>  Server started at <font color="#729FCF"><u style="text-decoration-style:solid">http://127.0.0.1:8000</u></font>
   <span style="background-color:#007166"><font color="#D3D7CF"> server </font></span>  Documentation at <font color="#729FCF"><u style="text-decoration-style:solid">http://127.0.0.1:8000/docs</u></font>

      <span style="background-color:#007166"><font color="#D3D7CF"> tip </font></span>  Running in development mode, for production use:
             <b>fastapi run</b>

             Logs:

     <span style="background-color:#007166"><font color="#D3D7CF"> INFO </font></span>  Will watch for changes in these directories:
             <b>[</b><font color="#4E9A06">&apos;/home/user/code/awesomeapp&apos;</font><b>]</b>
     <span style="background-color:#007166"><font color="#D3D7CF"> INFO </font></span>  Uvicorn running on <font color="#729FCF"><u style="text-decoration-style:solid">http://127.0.0.1:8000</u></font> <b>(</b>Press CTRL+C
             to quit<b>)</b>
     <span style="background-color:#007166"><font color="#D3D7CF"> INFO </font></span>  Started reloader process <b>[</b><font color="#34E2E2"><b>383138</b></font><b>]</b> using WatchFiles
     <span style="background-color:#007166"><font color="#D3D7CF"> INFO </font></span>  Started server process <b>[</b><font color="#34E2E2"><b>383153</b></font><b>]</b>
     <span style="background-color:#007166"><font color="#D3D7CF"> INFO </font></span>  Waiting for application startup.
     <span style="background-color:#007166"><font color="#D3D7CF"> INFO </font></span>  Application startup complete.
```

In the output, there's a line with something like:

```hl_lines="4"
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

That line shows the URL where your app is being served on your local machine.

Open your browser at [http://127.0.0.1:8000](http://127.0.0.1:8000).

You will see the JSON response as:

```JSON
{"message": "Hello World"}
```
````

[x] verified against live page

### Code-bearing chunks — 2 · chunk `4465`

- **id:** 4465
- **heading_path:** Path Parameters { #path-parameters } > Predefined values { #predefined-values } > Declare a *path parameter* { #declare-a-path-parameter }
- **source_url:** [https://fastapi.tiangolo.com/tutorial/path-params/](https://fastapi.tiangolo.com/tutorial/path-params/)

````text
Then create a *path parameter* with a type annotation using the enum class you created (`ModelName`):

```python
from enum import Enum

from fastapi import FastAPI


class ModelName(str, Enum):
    alexnet = "alexnet"
    resnet = "resnet"
    lenet = "lenet"


app = FastAPI()


@app.get("/models/{model_name}")
async def get_model(model_name: ModelName):
    if model_name is ModelName.alexnet:
        return {"model_name": model_name, "message": "Deep Learning FTW!"}

    if model_name.value == "lenet":
        return {"model_name": model_name, "message": "LeCNN all the images"}

    return {"model_name": model_name, "message": "Have some residuals"}
```

Because the available values for the *path parameter* are predefined, the interactive docs can show them nicely:

The value of the *path parameter* will be an *enumeration member*.
````

[x] verified against live page

### Code-bearing chunks — 3 · chunk `3238`

- **id:** 3238
- **heading_path:** OpenAPI Callbacks { #openapi-callbacks } > The normal **FastAPI** app { #the-normal-fastapi-app }
- **source_url:** [https://fastapi.tiangolo.com/advanced/openapi-callbacks/](https://fastapi.tiangolo.com/advanced/openapi-callbacks/)

````text
Let's first see how the normal API app would look before adding the callback.

It will have a *path operation* that will receive an `Invoice` body, and a query parameter `callback_url` that will contain the URL for the callback.

This part is pretty normal, most of the code is probably already familiar to you:

```python
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, HttpUrl

app = FastAPI()


class Invoice(BaseModel):
    id: str
    title: str | None = None
    customer: str
    total: float


class InvoiceEvent(BaseModel):
    description: str
    paid: bool


class InvoiceEventReceived(BaseModel):
    ok: bool


invoices_callback_router = APIRouter()


@invoices_callback_router.post(
    "{$callback_url}/invoices/{$request.body.id}", response_model=InvoiceEventReceived
)
def invoice_notification(body: InvoiceEvent):
    pass


@app.post("/invoices/", callbacks=invoices_callback_router.routes)
def create_invoice(invoice: Invoice, callback_url: HttpUrl | None = None):
    """
    Create an invoice.

    This will (let's imagine) let the API user (some external developer) create an
    invoice.

    And this path operation will:

    * Send the invoice to the client.
    * Collect the money from the client.
    * Send a notification back to the API user (the external developer), as a callback.
        * At this point is that the API will somehow send a POST request to the
            external API with the notification of the invoice event
            (e.g. "payment successful").
    """
    # Send the invoice, collect the money, send the notification (the callback)
    return {"msg": "Invoice received"}
```

/// tip

The `callback_url` query parameter uses a Pydantic [Url](https://docs.pydantic.dev/latest/api/networks/) type.

///

The only new thing is the `callbacks=invoices_callback_router.routes` as an argument to the *path operation decorator*. We'll see what that is next.
````

[x] verified against live page

### Code-bearing chunks — 4 · chunk `4232`

- **id:** 4232
- **heading_path:** Bigger Applications - Multiple Files { #bigger-applications-multiple-files } > The main `FastAPI` { #the-main-fastapi } > Import the `APIRouter` { #import-the-apirouter }
- **source_url:** [https://fastapi.tiangolo.com/tutorial/bigger-applications/](https://fastapi.tiangolo.com/tutorial/bigger-applications/)

````text
Now we import the other submodules that have `APIRouter`s:

`app/main.py`

```python
from fastapi import Depends, FastAPI

from .dependencies import get_query_token, get_token_header
from .internal import admin
from .routers import items, users

app = FastAPI(dependencies=[Depends(get_query_token)])


app.include_router(users.router)
app.include_router(items.router)
app.include_router(
    admin.router,
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_token_header)],
    responses={418: {"description": "I'm a teapot"}},
)


@app.get("/")
async def root():
    return {"message": "Hello Bigger Applications!"}
```

As the files `app/routers/users.py` and `app/routers/items.py` are submodules that are part of the same Python package `app`, we can use a single dot `.` to import them using "relative imports".
````

[x] verified against live page

### Code-bearing chunks — 5 · chunk `4227`

- **id:** 4227
- **heading_path:** Bigger Applications - Multiple Files { #bigger-applications-multiple-files } > Another module with `APIRouter` { #another-module-with-apirouter } > Import the dependencies { #import-the-dependencies }
- **source_url:** [https://fastapi.tiangolo.com/tutorial/bigger-applications/](https://fastapi.tiangolo.com/tutorial/bigger-applications/)

````text
This code lives in the module `app.routers.items`, the file `app/routers/items.py`.

And we need to get the dependency function from the module `app.dependencies`, the file `app/dependencies.py`.

So we use a relative import with `..` for the dependencies:

`app/routers/items.py`

```python
from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_token_header

router = APIRouter(
    prefix="/items",
    tags=["items"],
    dependencies=[Depends(get_token_header)],
    responses={404: {"description": "Not found"}},
)


fake_items_db = {"plumbus": {"name": "Plumbus"}, "gun": {"name": "Portal Gun"}}


@router.get("/")
async def read_items():
    return fake_items_db


@router.get("/{item_id}")
async def read_item(item_id: str):
    if item_id not in fake_items_db:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"name": fake_items_db[item_id]["name"], "item_id": item_id}


@router.put(
    "/{item_id}",
    tags=["custom"],
    responses={403: {"description": "Operation forbidden"}},
)
async def update_item(item_id: str):
    if item_id != "plumbus":
        raise HTTPException(
            status_code=403, detail="You can only update the item: plumbus"
        )
    return {"item_id": item_id, "name": "The great Plumbus"}
```
````

[x] verified against live page
