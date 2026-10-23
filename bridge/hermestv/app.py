import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator

from hermestv import config, hermes, history, picks, scenery, schedule, slots, watchlog, weather
from hermestv.picks import PicksOut
from hermestv.schedule import ScheduleOut
from hermestv.titles import _norm
from hermestv.weather import Weather
from hermestv.youtube import VIDEO_ID

log = logging.getLogger(__name__)
# uvicorn configures only its own loggers; without this the hermestv INFO lines never reach journalctl.
logging.basicConfig(format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("hermestv").setLevel(logging.INFO)


class _ItemIn(BaseModel):
    kind: Literal["film", "series", "video"]
    title: str = Field(min_length=1, max_length=300)
    year: int | None = Field(None, ge=1880, le=2100)
    video_id: str | None = None

    @model_validator(mode="after")
    def _check_item(self):
        if self.video_id is not None and (self.kind != "video" or not VIDEO_ID.fullmatch(self.video_id)):
            raise ValueError("video_id must be a YouTube video id, on a video only")
        # The key of a film or series is its normalised title.
        if self.kind != "video" and not _norm(self.title):
            raise ValueError("the title has no letters or digits")
        return self


class OpenedIn(_ItemIn):
    url: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _video_has_id(self):
        if self.kind == "video" and not self.video_id:
            raise ValueError("a video needs its video_id")
        return self


class HideIn(_ItemIn):
    channel: str | None = Field(None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _video_has_id_or_channel(self):
        if self.channel is not None and (self.kind != "video" or not _norm(self.channel)):
            raise ValueError("channel must have letters or digits, on a video only")
        if self.kind == "video" and not (self.video_id or self.channel):
            raise ValueError("a video needs its video_id or channel")
        return self


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    settings = config.load()
    if not settings.tv_key:
        log.warning("TV_KEY is empty: every /v1/* request is rejected")
    vault = asyncio.create_task(watchlog.vault_daily()) if settings.hermes_vault_tv_dir else None
    yield
    if vault:
        vault.cancel()
    if watchlog._pending:
        watchlog._pending.cancel()


app = FastAPI(title="hermes-tv-bridge", lifespan=_lifespan)


def _check_bearer(authorization: str, key: str) -> None:
    scheme, _, token = authorization.partition(" ")
    if not key or scheme.lower() != "bearer" or not hmac.compare_digest(token.encode(), key.encode()):
        raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})


def _auth(authorization: str = Header("")) -> None:
    _check_bearer(authorization, config.load().tv_key)


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "hermes": await hermes.healthy()}


@app.get("/v1/health", dependencies=[Depends(_auth)])
async def v1_health() -> dict:
    # "hermes" alone is what TV builds without the enabled field read.
    return {"hermes": await hermes.healthy(), "enabled": hermes.enabled()}


@app.get("/v1/picks", dependencies=[Depends(_auth)], response_model=PicksOut)
async def get_picks(row: Literal["films", "series", "youtube"] = "films") -> PicksOut:
    return picks.get(slots.current(), row)


@app.get("/v1/weather", dependencies=[Depends(_auth)], response_model=Weather)
async def get_weather():
    current = await weather.current()
    if current is None:
        # Without a location there is no weather by choice: not a failure.
        return JSONResponse({"error": "weather unavailable"}, status_code=503 if weather.enabled() else 200)
    return current


@app.post("/v1/opened", dependencies=[Depends(_auth)])
async def post_opened(body: OpenedIn) -> dict:
    history.opened(body.kind, body.title, body.year, body.video_id, body.url, slots.current().name)
    return {"ok": True}


@app.post("/v1/hide", dependencies=[Depends(_auth)])
async def post_hide(body: HideIn) -> dict:
    history.hide(body.kind, body.title, body.year, body.video_id, body.channel)
    return {"ok": True}


@app.get("/v1/schedule", dependencies=[Depends(_auth)], response_model=ScheduleOut)
async def get_schedule() -> ScheduleOut:
    return ScheduleOut(items=await schedule.upcoming())


@app.get("/v1/scenery", dependencies=[Depends(_auth)])
async def get_scenery() -> dict:
    now = scenery.period()
    return {"period": now, "items": scenery.items(now)}


@app.get("/v1/scenery/{period}/{name}", dependencies=[Depends(_auth)])
async def get_scenery_photo(period: str, name: str) -> FileResponse:
    path = scenery.photo(period, name)
    if path is None:
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})
