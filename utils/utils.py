from .logger import LOGGER
try:
    from pyrogram.raw.types import InputChannel
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from pyrogram.errors.exceptions.forbidden_403 import GroupcallForbidden    
    from apscheduler.jobstores.mongodb import MongoDBJobStore
    from apscheduler.jobstores.base import ConflictingIdError
    from pyrogram.raw.functions.channels import GetFullChannel
    from urllib.parse import urlparse, parse_qs
    from pytgcalls import StreamType
    import yt_dlp
    from pyrogram import filters
    from pymongo import MongoClient
    from datetime import datetime
    from threading import Thread
    from math import gcd
    from .pyro_dl import Downloader
    from config import Config
    from asyncio import sleep  
    from bot import bot
    from pyrogram import enums
    from PTN import parse
    import subprocess
    import asyncio
    import requests
    import json
    import random
    import time
    import sys
    import base64
    import os
    import math
    from pyrogram.errors.exceptions.bad_request_400 import (
        BadRequest, 
        ScheduleDateInvalid,
        PeerIdInvalid,
        ChannelInvalid
    )
    from pytgcalls.types.input_stream import (
        AudioVideoPiped, 
        AudioPiped,
        AudioImagePiped
    )
    from pytgcalls.types.input_stream import (
        AudioParameters,
        VideoParameters
    )
    from pyrogram.types import (
        InlineKeyboardButton, 
        InlineKeyboardMarkup, 
        Message
    )
    from pyrogram.raw.functions.phone import (
        EditGroupCallTitle, 
        CreateGroupCall,
        ToggleGroupCallRecord,
        StartScheduledGroupCall 
    )
    from pytgcalls.exceptions import (
        GroupCallNotFound, 
        NoActiveGroupCall,
        InvalidVideoProportion
    )
    from PIL import (
        Image, 
        ImageFont, 
        ImageDraw 
    )
    from user import (
        group_call, 
        USER
    )
except ModuleNotFoundError:
    import os
    import sys
    import subprocess
    file = os.path.abspath("requirements.txt")
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', '-r', file, '--upgrade'])
    os.execl(sys.executable, sys.executable, *sys.argv)

if Config.DATABASE_URI:
    from .database import db
    monclient = MongoClient(Config.DATABASE_URI)
    jobstores = {
        'default': MongoDBJobStore(client=monclient, database=Config.DATABASE_NAME, collection='scheduler')
    }
    scheduler = AsyncIOScheduler(jobstores=jobstores)
else:
    scheduler = AsyncIOScheduler()

async def run_loop_scheduler():
    scheduler.start()

asyncio.get_event_loop().run_until_complete(run_loop_scheduler())
dl = Downloader()

# Initialize chat-specific state
if not hasattr(Config, 'CHAT_STATES'):
    Config.CHAT_STATES = {}  # {chat_id: {'playlist': [], 'call_status': False, 'stream_link': False, ...}}

def get_spotify_access_token():
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "client_credentials",
        "client_id": Config.SPOTIFY_CLIENT_ID,
        "client_secret": Config.SPOTIFY_CLIENT_SECRET
    }
    response = requests.post(Config.SPOTIFY_TOKEN_URL, headers=headers, data=data)
    response.raise_for_status()
    return response.json().get("access_token")

def get_track_id_from_url(spotify_url):
    parsed_url = urlparse(spotify_url)
    path_segments = parsed_url.path.split("/")
    if len(path_segments) > 2 and path_segments[1] == "track":
        return path_segments[2]
    return None

def get_song_and_artist(spotify_url):
    track_id = get_track_id_from_url(spotify_url)
    if not track_id:
        raise ValueError("Invalid Spotify track URL")
    access_token = get_spotify_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    response = requests.get(f"{Config.SPOTIFY_TRACK_API_URL}{track_id}", headers=headers)
    response.raise_for_status()
    track_info = response.json()
    song_name = track_info.get("name")
    artist_name = ", ".join(artist["name"] for artist in track_info.get("artists", []))
    return song_name, artist_name

async def get_chat_state(chat_id):
    if chat_id not in Config.CHAT_STATES:
        Config.CHAT_STATES[chat_id] = {
            'playlist': [],
            'call_status': False,
            'stream_link': False,
            'is_active': False,
            'pause': False,
            'duration': {},
            'file_data': {},
            'schedule_list': [],
            'scheduled_stream': {},
            'has_schedule': False,
            'get_file': {},
            'muted': False,
            'volume': 100
        }
    return await config.get_chat_state(chat_id)

async def play(chat_id):
    state = await get_chat_state(chat_id)
    song = state['playlist'][0]
    if song[3] == "telegram":
        file = state['get_file'].get(song[5])
        if not file:
            file = await dl.pyro_dl(song[2])
            if not file:
                LOGGER.info("Downloading file from Telegram")
                file = await bot.download_media(song[2])
            state['get_file'][song[5]] = file
            await sleep(3)
        while not os.path.exists(file):
            file = state['get_file'].get(song[5])
            await sleep(1)
        total = int(((song[5].split("_")[1])) * 0.005
        while not (os.stat(file).st_size) >= total:
            LOGGER.info("Waiting for download")
            await sleep(1)
    elif song[3] == "url":
        file = song[2]
    else:
        file = await get_link(song[2])
    if not file:
        if state['playlist'] or state['stream_link']:
            return await skip(chat_id)
        else:
            LOGGER.error("This stream is not supported, leaving VC.")
            await leave_call(chat_id)
            return False
    link, seek, pic, width, height = await check_the_media(file, title=f"{song[1]}")
    if not link:
        LOGGER.warning("Unsupported link, Skipping from queue.")
        return
    await sleep(1)
    if state['stream_link']:
        state['stream_link'] = False
    LOGGER.info(f"STARTING PLAYING in {chat_id}: {song[1]}")
    await join_call(chat_id, link, seek, pic, width, height)

async def schedule_a_play(chat_id, job_id, date):
    state = await get_chat_state(chat_id)
    try:
        scheduler.add_job(run_schedule, "date", [chat_id, job_id], id=f"{chat_id}_{job_id}", run_date=date, max_instances=50, misfire_grace_time=None)
    except ConflictingIdError:
        LOGGER.warning(f"Schedule {job_id} already exists in {chat_id}")
        return
    if not state['call_status'] or not state['is_active']:
        if state['schedule_list'] and state['schedule_list'][0]['job_id'] == job_id \
                and (date - datetime.now()).total_seconds() < 86400:
            song = state['scheduled_stream'].get(job_id)
            if Config.IS_RECORDING:
                scheduler.add_job(start_record_stream, "date", id=f"record_{chat_id}", run_date=date, max_instances=50, misfire_grace_time=None)
            try:
                await USER.invoke(CreateGroupCall(
                    peer=(await USER.resolve_peer(chat_id)),
                    random_id=random.randint(10000, 999999999),
                    schedule_date=int(date.timestamp()),
                    title=song['1']
                ))
                state['has_schedule'] = True
            except ScheduleDateInvalid:
                LOGGER.error(f"Unable to schedule VideoChat in {chat_id}")
            except Exception as e:
                LOGGER.error(f"Error in scheduling voicechat in {chat_id} - {e}", exc_info=True)
    await sync_to_db()

async def run_schedule(chat_id, job_id):
    state = await get_chat_state(chat_id)
    data = state['scheduled_stream'].get(job_id)
    if not data:
        LOGGER.error(f"Scheduled stream {job_id} in {chat_id} not played, data missing")
        old = [k for k in state['schedule_list'] if k['job_id'] == job_id]
        if old:
            state['schedule_list'].remove(old[0])
        await sync_to_db()
        return
    if state['has_schedule']:
        if not await start_scheduled(chat_id):
            LOGGER.error(f"Scheduled stream in {chat_id} skipped, unable to start voice chat")
            return
    data_ = [{1: data['1'], 2: data['2'], 3: data['3'], 4: data['4'], 5: data['5']}]
    state['playlist'] = data_ + state['playlist']
    await play(chat_id)
    LOGGER.info(f"Starting Scheduled Stream in {chat_id}")
    del state['scheduled_stream'][job_id]
    old = [k for k in state['schedule_list'] if k['job_id'] == job_id]
    if old:
        state['schedule_list'].remove(old[0])
    if not state['schedule_list']:
        state['scheduled_stream'] = {}
    await sync_to_db()
    if len(state['playlist']) <= 1:
        return
    await download(chat_id, state['playlist'][1])

async def cancel_all_schedules(chat_id):
    state = await get_chat_state(chat_id)
    for sch in state['schedule_list']:
        job = sch['job_id']
        k = scheduler.get_job(f"{chat_id}_{job}", jobstore=None)
        if k:
            scheduler.remove_job(f"{chat_id}_{job}", jobstore=None)
        if state['scheduled_stream'].get(job):
            del state['scheduled_stream'][job]
    state['schedule_list'].clear()
    await sync_to_db()
    LOGGER.info(f"All schedules removed in {chat_id}")

async def skip(chat_id):
    state = await get_chat_state(chat_id)
    if state['stream_link'] and len(state['playlist']) == 0 and Config.IS_LOOP:
        await stream_from_link(chat_id)
        return
    elif not state['playlist'] and Config.IS_LOOP:
        LOGGER.info(f"Loop Play enabled in {chat_id}, switching to STARTUP_STREAM")
        await start_stream(chat_id)
        return
    elif not state['playlist'] and not Config.IS_LOOP:
        LOGGER.info(f"Loop Play disabled in {chat_id}, leaving call")
        await leave_call(chat_id)
        return
    old_track = state['playlist'].pop(0)
    await clear_db_playlist(chat_id, song=old_track)
    if old_track[3] == "telegram":
        file = state['get_file'].get(old_track[5])
        if file:
            try:
                os.remove(file)
            except:
                pass
            del state['get_file'][old_track[5]]
    if not state['playlist'] and Config.IS_LOOP:
        LOGGER.info(f"Loop Play enabled in {chat_id}, switching to STARTUP_STREAM")
        await start_stream(chat_id)
        return
    elif not state['playlist'] and not Config.IS_LOOP:
        LOGGER.info(f"Loop Play disabled in {chat_id}, leaving call")
        await leave_call(chat_id)
        return
    LOGGER.info(f"START PLAYING in {chat_id}: {state['playlist'][0][1]}")
    if state['duration'].get('PAUSE'):
        del state['duration']['PAUSE']
    await play(chat_id)
    if len(state['playlist']) <= 1:
        return
    await download(chat_id, state['playlist'][1])

async def check_vc(chat_id):
    a = await bot.invoke(GetFullChannel(channel=(await bot.resolve_peer(chat_id))))
    if a.full_chat.call is None:
        try:
            LOGGER.info(f"No active calls found in {chat_id}, creating new")
            await USER.invoke(CreateGroupCall(
                peer=(await USER.resolve_peer(chat_id)),
                random_id=random.randint(10000, 999999999)
            ))
            if Config.WAS_RECORDING:
                await start_record_stream(chat_id)
            await sleep(2)
            return True
        except Exception as e:
            LOGGER.error(f"Unable to start new GroupCall in {chat_id} - {e}", exc_info=True)
            return False
    else:
        state = await get_chat_state(chat_id)
        if state['has_schedule']:
            await start_scheduled(chat_id)
        return True

async def join_call(chat_id, link, seek, pic, width, height):
    state = await get_chat_state(chat_id)
    if not await check_vc(chat_id):
        LOGGER.error(f"No voice call found in {chat_id} and unable to create one")
        return
    if state['has_schedule']:
        await start_scheduled(chat_id)
    if state['call_status']:
        if not state['is_active']:
            state['call_status'] = False
            return await join_call(chat_id, link, seek, pic, width, height)
        play = await change_file(chat_id, link, seek, pic, width, height)
    else:
        play = await join_and_play(chat_id, link, seek, pic, width, height)
    if play == False:
        await sleep(1)
        await join_call(chat_id, link, seek, pic, width, height)
    await sleep(1)
    if not seek:
        state['duration']["TIME"] = time.time()
        if Config.EDIT_TITLE:
            await edit_title(chat_id)
    old = state['get_file'].get("old")
    if old:
        for file in old:
            try:
                os.remove(f"./downloads/{file}")
            except:
                pass
        try:
            del state['get_file']["old"]
        except:
            LOGGER.error("Error in deleting old files")
    await send_playlist(chat_id)

async def start_scheduled(chat_id):
    state = await get_chat_state(chat_id)
    try:
        await USER.invoke(
            StartScheduledGroupCall(
                call=(
                    await USER.invoke(
                        GetFullChannel(
                            channel=(await USER.resolve_peer(chat_id))
                        )
                    ).full_chat.call
                )
            )
        )
        if Config.WAS_RECORDING:
            await start_record_stream(chat_id)
        return True
    except Exception as e:
        if 'GROUPCALL_ALREADY_STARTED' in str(e):
            LOGGER.warning(f"Group call already started in {chat_id}")
            return True
        else:
            state['has_schedule'] = False
            return await check_vc(chat_id)

async def join_and_play(chat_id, link, seek, pic, width, height):
    state = await get_chat_state(chat_id)
    try:
        if seek:
            start = str(seek['start'])
            end = str(seek['end'])
            if not Config.IS_VIDEO:
                await group_call.join_group_call(
                    int(chat_id),
                    AudioPiped(
                        link,
                        audio_parameters=AudioParameters(Config.BITRATE),
                        additional_ffmpeg_parameters=f'-ss {start} -atend -t {end}',
                    ),
                    stream_type=StreamType().pulse_stream,
                )
            else:
                if pic:
                    cwidth, cheight = resize_ratio(1280, 720, Config.CUSTOM_QUALITY)
                    await group_call.join_group_call(
                        int(chat_id),
                        AudioImagePiped(
                            link,
                            pic,
                            video_parameters=VideoParameters(cwidth, cheight, Config.FPS),
                            audio_parameters=AudioParameters(Config.BITRATE),
                            additional_ffmpeg_parameters=f'-ss {start} -atend -t {end}',
                        ),
                        stream_type=StreamType().pulse_stream,
                    )
                else:
                    if not width or not height:
                        LOGGER.error(f"No valid video found in {chat_id}")
                        if state['playlist'] or state['stream_link']:
                            return await skip(chat_id)
                        else:
                            LOGGER.error(f"Stream not supported in {chat_id}, leaving VC")
                            return
                    cwidth, cheight = resize_ratio(width, height, Config.CUSTOM_QUALITY)
                    await group_call.join_group_call(
                        int(chat_id),
                        AudioVideoPiped(
                            link,
                            video_parameters=VideoParameters(cwidth, cheight, Config.FPS),
                            audio_parameters=AudioParameters(Config.BITRATE),
                            additional_ffmpeg_parameters=f'-ss {start} -atend -t {end}',
                        ),
                        stream_type=StreamType().pulse_stream,
                    )
        else:
            if not Config.IS_VIDEO:
                await group_call.join_group_call(
                    int(chat_id),
                    AudioPiped(
                        link,
                        audio_parameters=AudioParameters(Config.BITRATE),
                    ),
                    stream_type=StreamType().pulse_stream,
                )
            else:
                if pic:
                    cwidth, cheight = resize_ratio(1280, 720, Config.CUSTOM_QUALITY)
                    await group_call.join_group_call(
                        int(chat_id),
                        AudioImagePiped(
                            link,
                            pic,
                            video_parameters=VideoParameters(cwidth, cheight, Config.FPS),
                            audio_parameters=AudioParameters(Config.BITRATE),
                        ),
                        stream_type=StreamType().pulse_stream,
                    )
                else:
                    if not width or not height:
                        LOGGER.error(f"No valid video found in {chat_id}")
                        if state['playlist'] or state['stream_link']:
                            return await skip(chat_id)
                        else:
                            LOGGER.error(f"Stream not supported in {chat_id}, leaving VC")
                            return
                    cwidth, cheight = resize_ratio(width, height, Config.CUSTOM_QUALITY)
                    await group_call.join_group_call(
                        int(chat_id),
                        AudioVideoPiped(
                            link,
                            video_parameters=VideoParameters(cwidth, cheight, Config.FPS),
                            audio_parameters=AudioParameters(Config.BITRATE),
                        ),
                        stream_type=StreamType().pulse_stream,
                    )
        state['call_status'] = True
        state['is_active'] = True
        return True
    except NoActiveGroupCall:
        try:
            LOGGER.info(f"No active calls found in {chat_id}, creating new")
            await USER.invoke(CreateGroupCall(
                peer=(await USER.resolve_peer(chat_id)),
                random_id=random.randint(10000, 999999999)
            ))
            if Config.WAS_RECORDING:
                await start_record_stream(chat_id)
            await sleep(2)
            await restart_playout(chat_id)
        except Exception as e:
            LOGGER.error(f"Unable to start new GroupCall in {chat_id} - {e}", exc_info=True)
    except InvalidVideoProportion:
        LOGGER.error(f"Unsupported video in {chat_id}")
        if state['playlist'] or state['stream_link']:
            return await skip(chat_id)
        else:
            LOGGER.error(f"Stream not supported in {chat_id}, leaving VC")
            return
    except Exception as e:
        LOGGER.error(f"Errors while joining call in {chat_id} - {e}", exc_info=True)
        return False

async def change_file(chat_id, link, seek, pic, width, height):
    state = await get_chat_state(chat_id)
    try:
        if seek:
            start = str(seek['start'])
            end = str(seek['end'])
            if not Config.IS_VIDEO:
                await group_call.change_stream(
                    int(chat_id),
                    AudioPiped(
                        link,
                        audio_parameters=AudioParameters(Config.BITRATE),
                        additional_ffmpeg_parameters=f'-ss {start} -atend -t {end}',
                    ),
                )
            else:
                if pic:
                    cwidth, cheight = resize_ratio(1280, 720, Config.CUSTOM_QUALITY)
                    await group_call.change_stream(
                        int(chat_id),
                        AudioImagePiped(
                            link,
                            pic,
                            video_parameters=VideoParameters(cwidth, cheight, Config.FPS),
                            audio_parameters=AudioParameters(Config.BITRATE),
                            additional_ffmpeg_parameters=f'-ss {start} -atend -t {end}',
                        ),
                    )
                else:
                    if not width or not height:
                        LOGGER.error(f"No valid video found in {chat_id}")
                        if state['playlist'] or state['stream_link']:
                            return await skip(chat_id)
                        else:
                            LOGGER.error(f"Stream not supported in {chat_id}, leaving VC")
                            return
                    cwidth, cheight = resize_ratio(width, height, Config.CUSTOM_QUALITY)
                    await group_call.change_stream(
                        int(chat_id),
                        AudioVideoPiped(
                            link,
                            video_parameters=VideoParameters(cwidth, cheight, Config.FPS),
                            audio_parameters=AudioParameters(Config.BITRATE),
                            additional_ffmpeg_parameters=f'-ss {start} -atend -t {end}',
                        ),
                    )
        else:
            if not Config.IS_VIDEO:
                await group_call.change_stream(
                    int(chat_id),
                    AudioPiped(
                        link,
                        audio_parameters=AudioParameters(Config.BITRATE),
                    ),
                )
            else:
                if pic:
                    cwidth, cheight = resize_ratio(1280, 720, Config.CUSTOM_QUALITY)
                    await group_call.change_stream(
                        int(chat_id),
                        AudioImagePiped(
                            link,
                            pic,
                            video_parameters=VideoParameters(cwidth, cheight, Config.FPS),
                            audio_parameters=AudioParameters(Config.BITRATE),
                        ),
                    )
                else:
                    if not width or not height:
                        LOGGER.error(f"No valid video found in {chat_id}")
                        if state['playlist'] or state['stream_link']:
                            return await skip(chat_id)
                        else:
                            LOGGER.error(f"Stream not supported in {chat_id}, leaving VC")
                            return
                    cwidth, cheight = resize_ratio(width, height, Config.CUSTOM_QUALITY)
                    await group_call.change_stream(
                        int(chat_id),
                        AudioVideoPiped(
                            link,
                            video_parameters=VideoParameters(cwidth, cheight, Config.FPS),
                            audio_parameters=AudioParameters(Config.BITRATE),
                        ),
                    )
    except InvalidVideoProportion:
        LOGGER.error(f"Invalid video in {chat_id}, skipped")
        if state['playlist'] or state['stream_link']:
            return await skip(chat_id)
        else:
            LOGGER.error(f"Stream not supported in {chat_id}, leaving VC")
            await leave_call(chat_id)
            return
    except Exception as e:
        LOGGER.error(f"Error in changing stream in {chat_id} - {e}", exc_info=True)
        return False

async def seek_file(chat_id, seektime):
    state = await get_chat_state(chat_id)
    play_start = int(float(state['duration'].get('TIME')))
    if not play_start:
        return False, "Player not yet started"
    else:
        data = state['file_data']
        if not data:
            return False, "No Streams for seeking"
        played = int(float(time.time())) - int(float(play_start))
        if data.get("dur", 0) == 0:
            return False, "Seems like live stream is playing, which cannot be seeked."
        total = int(float(data.get("dur", 0)))
        trimend = total - played - int(seektime)
        trimstart = played + int(seektime)
        if trimstart > total:
            return False, "Seeked duration exceeds maximum duration of file"
        new_play_start = int(play_start) - int(seektime)
        state['duration']['TIME'] = new_play_start
        link, seek, pic, width, height = await check_the_media(data.get("file"), seek={"start": trimstart, "end": trimend})
        await join_call(chat_id, link, seek, pic, width, height)
        return True, None

async def leave_call(chat_id):
    state = await get_chat_state(chat_id)
    try:
        await group_call.leave_group_call(chat_id)
    except Exception as e:
        LOGGER.error(f"Errors while leaving call in {chat_id} - {e}", exc_info=True)
    state['call_status'] = False
    state['is_active'] = False
    if state['stream_link']:
        state['stream_link'] = False
    if state['schedule_list']:
        sch = state['schedule_list'][0]
        if (sch['date'] - datetime.now()).total_seconds() < 86400:
            song = state['scheduled_stream'].get(sch['job_id'])
            if Config.IS_RECORDING:
                k = scheduler.get_job(f"record_{chat_id}", jobstore=None)
                if k:
                    scheduler.remove_job(f"record_{chat_id}", jobstore=None)
                scheduler.add_job(start_record_stream, "date", id=f"record_{chat_id}", args=[chat_id], run_date=sch['date'], max_instances=50, misfire_grace_time=None)
            try:
                await USER.invoke(CreateGroupCall(
                    peer=(await USER.resolve_peer(chat_id)),
                    random_id=random.randint(10000, 999999999),
                    schedule_date=int((sch['date']).timestamp()),
                    title=song['1']
                ))
                state['has_schedule'] = True
            except ScheduleDateInvalid:
                LOGGER.error(f"Unable to schedule VideoChat in {chat_id}")
            except Exception as e:
                LOGGER.error(f"Error in scheduling voicechat in {chat_id} - {e}", exc_info=True)
    await sync_to_db()

async def restart(chat_id):
    state = await get_chat_state(chat_id)
    try:
        await group_call.leave_group_call(chat_id)
        await sleep(2)
    except Exception as e:
        LOGGER.error(f"Error restarting in {chat_id} - {e}", exc_info=True)
    if not state['playlist']:
        await start_stream(chat_id)
        return
    LOGGER.info(f"START PLAYING in {chat_id}: {state['playlist'][0][1]}")
    await sleep(1)
    await play(chat_id)
    LOGGER.info(f"Restarting Playout in {chat_id}")
    if len(state['playlist']) <= 1:
        return
    await download(chat_id, state['playlist'][1])

async def restart_playout(chat_id):
    state = await get_chat_state(chat_id)
    if not state['playlist']:
        await start_stream(chat_id)
        return
    LOGGER.info(f"RESTART PLAYING in {chat_id}: {state['playlist'][0][1]}")
    data = state['file_data']
    if data:
        link, seek, pic, width, height = await check_the_media(data['file'], title=f"{state['playlist'][0][1]}")
        if not link:
            LOGGER.warning(f"Unsupported Link in {chat_id}")
            return
        await sleep(1)
        if state['stream_link']:
            state['stream_link'] = False
        await join_call(chat_id, link, seek, pic, width, height)
    else:
        await play(chat_id)
    if len(state['playlist']) <= 1:
        return
    await download(chat_id, state['playlist'][1])

def is_ytdl_supported(input_url: str) -> bool:
    shei = yt_dlp.extractor.gen_extractors()
    return any(int_extraactor.suitable(input_url) and int_extraactor.IE_NAME != "generic" for int_extraactor in shei)

async def set_up_startup():
    Config.YSTREAM = False
    Config.YPLAY = False
    Config.CPLAY = False
    if Config.STREAM_URL.startswith("@") or (str(Config.STREAM_URL)).startswith("-100"):
        Config.CPLAY = True
        LOGGER.info(f"Channel Play enabled from {Config.STREAM_URL}")
        Config.STREAM_SETUP = True
        return
    elif Config.STREAM_URL.startswith("https://t.me/DumpPlaylist"):
        Config.YPLAY = True
        LOGGER.info("YouTube Playlist is set as STARTUP STREAM")
        Config.STREAM_SETUP = True
        return
    match = is_ytdl_supported(Config.STREAM_URL)
    if match:
        Config.YSTREAM = True
        LOGGER.info("YouTube Stream is set as STARTUP STREAM")
    else:
        LOGGER.info("Direct link set as STARTUP_STREAM")
    Config.STREAM_SETUP = True

async def start_stream(chat_id):
    state = await get_chat_state(chat_id)
    if not Config.STREAM_SETUP:
        await set_up_startup()
    if Config.YPLAY:
        try:
            msg_id = Config.STREAM_URL.split("/", 4)[4]
        except:
            LOGGER.error("Unable to fetch YouTube playlist")
            return
        await y_play(chat_id, int(msg_id))
        return
    elif Config.CPLAY:
        await c_play(chat_id, Config.STREAM_URL)
        return
    elif Config.YSTREAM:
        link = await get_link(Config.STREAM_URL)
    else:
        link = Config.STREAM_URL
    link, seek, pic, width, height = await check_the_media(link, title="Startup Stream")
    if not link:
        LOGGER.warning(f"Unsupported link in {chat_id}")
        return False
    if Config.IS_VIDEO:
        if not ((width and height) or pic):
            LOGGER.error(f"Stream Link invalid in {chat_id}")
            return
    await join_call(chat_id, link, seek, pic, width, height)

async def stream_from_link(chat_id, link=None):
    state = await get_chat_state(chat_id)
    link, seek, pic, width, height = await check_the_media(link)
    if not link:
        LOGGER.error(f"Unable to obtain sufficient information from URL in {chat_id}")
        return False, "Unable to obtain sufficient information from the given URL"
    state['stream_link'] = link
    await join_call(chat_id, link, seek, pic, width, height)
    return True, None

async def get_link(file):
    ytdl_cmd = [
        "yt-dlp",
        "--geo-bypass",
        "-g",
        "-f", "best[height<=?720][width<=?1280]/best",
        "--cookies", Config.YT_COOKIES_PATH,
        "--no-warnings",
        file
    ]
    process = await asyncio.create_subprocess_exec(
        *ytdl_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    output, err = await process.communicate()
    if not output:
        LOGGER.error(str(err.decode()))
        return False
    stream = output.decode().strip()
    link = (stream.split("\n"))[-1]
    if link:
        return link
    else:
        LOGGER.error("Unable to get sufficient info from link")
        return False

async def download(chat_id, song, msg=None):
    state = await get_chat_state(chat_id)
    if song[3] == "telegram":
        if not state['get_file'].get(song[5]):
            try:
                original_file = await dl.pyro_dl(song[2])
                state['get_file'][song[5]] = original_file
                return original_file
            except Exception as e:
                LOGGER.error(f"Error downloading Telegram file in {chat_id} - {e}", exc_info=True)
                state['playlist'].remove(song)
                await clear_db_playlist(chat_id, song=song)
                if len(state['playlist']) <= 1:
                    return
                await download(chat_id, state['playlist'][1])

async def check_the_media(link, seek=False, pic=False, title="Music"):
    if not Config.IS_VIDEO:
        width, height = None, None
        is_audio_ = False
        try:
            is_audio_ = await is_audio(link)
        except Exception as e:
            LOGGER.error(f"Error checking audio - {e}", exc_info=True)
            is_audio_ = False
        if not is_audio_:
            LOGGER.error("No Audio Source found")
            return None, None, None, None, None
    else:
        if os.path.isfile(link) and "audio" in (Config.CHAT_STATES.get(chat_id, {}).get('playlist', [{}])[0].get(5, '')):
            width, height = None, None
        else:
            try:
                width, height = await get_height_and_width(link)
            except Exception as e:
                LOGGER.error(f"Error getting video properties - {e}", exc_info=True)
                width, height = None, None
        if not width or not height:
            is_audio_ = False
            try:
                is_audio_ = await is_audio(link)
            except:
                is_audio_ = False
                LOGGER.error("Unable to get Audio properties within time")
            if is_audio_:
                pic_ = await bot.get_messages("DumpPlaylist", 30)
                photo = "./pic/photo"
                if not os.path.exists(photo):
                    photo = await pic_.download(file_name=photo)
                try:
                    dur_ = await get_duration(link)
                except:
                    dur_ = 0
                pic = get_image(title, photo, dur_)
            else:
                return None, None, None, None, None
    try:
        dur = await get_duration(link)
    except:
        dur = 0
    chat_id = list(Config.CHAT_STATES.keys())[0] if Config.CHAT_STATES else None
    if chat_id:
        state = await get_chat_state(chat_id)
        state['file_data'] = {"file": link, 'dur': dur}
    return link, seek, pic, width, height

async def edit_title(chat_id):
    state = await get_chat_state(chat_id)
    if state['stream_link']:
        title = "Live Stream"
    elif state['playlist']:
        title = state['playlist'][0][1]
    else:
        title = "Live Stream"
    try:
        chat = await USER.resolve_peer(chat_id)
        full_chat = await USER.invoke(
            GetFullChannel(
                channel=InputChannel(
                    channel_id=chat.channel_id,
                    access_hash=chat.access_hash,
                ),
            )
        )
        edit = EditGroupCallTitle(call=full_chat.full_chat.call, title=title)
        await USER.invoke(edit)
    except GroupcallForbidden as e:
        LOGGER.error(f"Cannot edit group call title in {chat_id}: {e}")
    except Exception as e:
        LOGGER.error(f"Error editing title in {chat_id}: {e}", exc_info=True)

async def stop_recording(chat_id):
    state = await get_chat_state(chat_id)
    job = f"record_{chat_id}"
    a = await bot.invoke(GetFullChannel(channel=(await bot.resolve_peer(chat_id))))
    if a.full_chat.call is None:
        k = scheduler.get_job(job_id=job, jobstore=None)
        if k:
            scheduler.remove_job(job, jobstore=None)
        Config.IS_RECORDING = False
        await sync_to_db()
        return False, "No GroupCall Found"
    try:
        await USER.invoke(
            ToggleGroupCallRecord(
                call=(
                    await USER.invoke(
                        GetFullChannel(
                            channel=(await USER.resolve_peer(chat_id))
                        )
                    ).full_chat.call,
                start=False,
            )
        )
        Config.IS_RECORDING = False
        Config.LISTEN = True
        await sync_to_db()
        k = scheduler.get_job(job_id=job, jobstore=None)
        if k:
            scheduler.remove_job(job, jobstore=None)
        return True, "Successfully Stopped Recording"
    except Exception as e:
        if 'GROUPCALL_NOT_MODIFIED' in str(e):
            LOGGER.warning(f"No recording exists in {chat_id}")
            Config.IS_RECORDING = False
            await sync_to_db()
            k = scheduler.get_job(job_id=job, jobstore=None)
            if k:
                scheduler.remove_job(job, jobstore=None)
            return False, "No recording was started"
        else:
            LOGGER.error(f"Error stopping recording in {chat_id}: {e}")
            Config.IS_RECORDING = False
            k = scheduler.get_job(job_id=job, jobstore=None)
            if k:
                scheduler.remove_job(job, jobstore=None)
            await sync_to_db()
            return False, str(e)

async def start_record_stream(chat_id):
    if Config.IS_RECORDING:
        await stop_recording(chat_id)
    if Config.WAS_RECORDING:
        Config.WAS_RECORDING = False
    a = await bot.invoke(GetFullChannel(channel=(await bot.resolve_peer(chat_id))))
    job = f"record_{chat_id}"
    if a.full_chat.call is None:
        k = scheduler.get_job(job_id=job, jobstore=None)
        if k:
            scheduler.remove_job(job, jobstore=None)
        return False, "No GroupCall Found"
    try:
        if not Config.PORTRAIT:
            pt = False
        else:
            pt = True
        if not Config.RECORDING_TITLE:
            tt = None
        else:
            tt = Config.RECORDING_TITLE
        if Config.IS_VIDEO_RECORD:
            await USER.invoke(
                ToggleGroupCallRecord(
                    call=(
                        await USER.invoke(
                            GetFullChannel(
                                channel=(await USER.resolve_peer(chat_id))
                            )
                        ).full_chat.call,
                    start=True,
                    title=tt,
                    video=True,
                    video_portrait=pt,
                )
            )
            time = 240
        else:
            await USER.invoke(
                ToggleGroupCallRecord(
                    call=(
                        await USER.invoke(
                            GetFullChannel(
                                channel=(await USER.resolve_peer(chat_id))
                            )
                        ).full_chat.call,
                    start=True,
                    title=tt,
                )
            )
            time = 86400
        Config.IS_RECORDING = True
        k = scheduler.get_job(job_id=job, jobstore=None)
        if k:
            scheduler.remove_job(job, jobstore=None)
        try:
            scheduler.add_job(renew_recording, "interval", id=job, minutes=time, args=[chat_id], max_instances=50, extra_ids=1)
        except ConflictingIdError:
            scheduler.remove_job(job_id=job, jobstore=None)
            scheduler.add_job(id=job, func=renew_recording, args=[chat_id], trigger='interval', minutes=time, max_instances=50, misfire_grace_time=3000)
            LOGGER.warning(f"Recording {job_id} {job} in {job_id} already scheduled, rescheduling to avoid conflicts")
    except Exception as e:
        if 'failed to start' in str(e.group_call):
            LOGGER.error(f"Failed to start recording for {job_id} in {job}")
            Config.RECORDING_STARTED = False
            k = scheduler.get_job(job_id=job_id, jobstore=None)
            if k:
                scheduler.remove_job(k=job_id, jobstore=None)
            await sync_to_db()
            return False, str(e.group_call)
        else:
            LOGGER.error(f"Recording error for {job_id} in {job}: {e}")
            Config.RECORDING_STARTED = False
            k = scheduler.get_job(job_id=job_id, jobstore=None)
            if k:
                scheduler.remove_job(k=job_id, jobstore=None)
            await sync_to_db()
            return False, str(e.job_id)

async def send_playlist(chat_id):
    if Config.LOG_ID:
        pl = await get_playlist_string(chat_id)
        if Config.msg.get(f'player_{chat_id}'):
            await Config.msg[f'player_{chat_id}'].delete()
        Config.msg[f'player_{chat_id}'] = await send_text(pl, chat_id)

async def send_text(text, chat_id):
    message = await bot.send_message(
        int(Config.LOG_ID),
        text,
        reply_markup=await get_buttons(chat_id),
        disable_web_page_preview=True,
        disable_notification=True
    )
    return message

async def shuffle_playlist(chat_id):
    state = await get_chat_state(chat_id)
    v = []
    p = [v.append(state['playlist'][c]) for c in range(2, len(state['playlist']))]
    random.shuffle(v)
    for c in range(2, len(state['playlist'])):
        state['playlist'].remove(state['playlist'][c])
        state['playlist'].insert(c, v[c-2])

async def import_play_list(chat_id, file):
    state = await get_chat_state(chat_id)
    file = open(file)
    try:
        f = json.loads(file.read(), object_hook=lambda d: {int(k): v for k, v in d.items()})
        for playf in f:
            state['playlist'].append(playf)
            await add_to_db_playlist(chat_id, playf)
            if len(state['playlist']) >= 1 and not state['call_status']:
                LOGGER.info(f"Extracting link and processing in {playf}...")
                await download(downloadf, state['playlist'][0])
                await play(chat_id)
            elif len(state['playlist']) == 1 and state['call_status']:
                LOGGER.info(f"Extracting link and processing in {chat_id}...")
                await download(download_id, state['playlist'][0])
                await play(chat_id)
        if not state['playlist']:
            file.close()
            try:
                os.remove(file)
            except:
                pass
            return False
        file.close()
        for track in state['playlist'][:2]:
            await download(chat_id, track)
        try:
            os.remove(file)
        except:
            pass
        return True
    except Exception as e:
        LOGGER.error(f"Error importing playlist in {chat_id} - {e}", exc_info=True)
        return False

async def y_play(chat_id, playlist):
    state = await get_chat_state(chat_id)
    try:
        getplaylist = await bot.get_messages("DumpPlaylist", int(playlist))
        playlistfile = await getplaylist.download()
        LOGGER.info(f"Trying to get playlist details in {chat_id}")
        n = await import_play_list(chat_id, playlistfile)
        if not n:
            LOGGER.error(f"Error importing playlist in {chat_id}")
            Config.YPLAY = False
            Config.YSTREAM = True
            if Config.IS_LOOP:
                Config.STREAM_URL = "https://www.youtube.com/watch?v=zcrUCvBD16k"
                LOGGER.info(f"Starting default stream in {chat_id}")
                await start_stream(chat_id)
            return False
        if Config.SHUFFLE:
            await shuffle_playlist(chat_id)
        return True
    except Exception as e:
        LOGGER.error(f"Error importing playlist in {chat_id} - {e}", exc_info=True)
        Config.YSTREAM = True
        Config.YPLAY = False
        if Config.IS_LOOP:
            Config.STREAM_URL = "https://www.youtube.com/watch?v=zcrUCvBD16k"
            LOGGER.info(f"Starting default stream in {chat_id}")
            await start_stream(chat_id)
        return False

async def c_play(chat_id, channel):
    state = await get_chat_state(chat_id)
    if (str(channel)).startswith("-100"):
        channel = int(channel)
    else:
        if channel.startswith("@"):
            channel = channel.replace("@", "")
    try:
        chat = await USER.get_chat(channel)
        LOGGER.info(f"Searching files from {channel} for {chat_id}")
        me = [enums.MessagesFilter.VIDEO, enums.MessagesFilter.DOCUMENT, enums.MessagesFilter.AUDIO]
        who = 0
        for filter in me:
            if filter in Config.FILTERS:
                async for m in USER.search_messages(chat_id=channel, filter=filter):
                    you = await bot.get_messages(channel, m.message_id)
                    now = datetime.now()
                    nyav = now.strftime("%d-%m-%Y-%H:%M:%S")
                    if filter == enums.MessagesFilter.AUDIO:
                        if you.audio.title is None:
                            if you.audio.file_name is None:
                                title_ = "Music"
                            else:
                                title_ = you.audio.file_name
                        else:
                            title_ = you.audio.title
                        if you.audio.performer is not None:
                            title = f"{you.audio.performer} - {title_}"
                        else:
                            title = title_
                        file_id = you.audio.file_id
                        unique = f"{nyav}_{you.audio.file_size}_audio"
                    elif filter == enums.MessagesFilter.VIDEO:
                        file_id = you.video.file_id
                        title = you.video.file_name
                        if Config.PTN:
                            ny = parse(title)
                            title_ = ny.get("title")
                            if title_:
                                title = title_
                        unique = f"{nyav}_{you.video.file_size}_video"
                    elif filter == enums.MessagesFilter.DOCUMENT:
                        if not "video" in you.document.mime_type:
                            continue
                        file_id = you.document.file_id
                        title = you.document.file_name
                        unique = f"{nyav}_{you.document.file_size}_document"
                        if Config.PTN:
                            ny = parse(title)
                            title_ = ny.get("title")
                            if title_:
                                title = title_
                    if title is None:
                        title = "Music"
                    data = {1": title, "2": file_id, "3": "telegram", "4": f"[{chat.title}]]({you.link})", "5": unique}
                    state['playlist'].append(data)
                    await add_to_db_playlist(chat_id, data)
                    who += 1
                    if not state['call_status'] and len(state['playlist']) >= 1:
                        LOGGER.info(f"Downloading {title} for {chat_id}")
                        await download(chat_id, state['download'])
                        await play(chat_id)
                        LOGGER.info(f"START PLAYING in {chat_id}: {title}")
                    elif len(state['playlist']) == 1 and state['call_status']:
                        LOGGER.info(f"Downloading {title} for {chat_id}")
                        await download(chat_id, state['playlist'][0])
                        await play(chat_id)
        if who == 0:
            LOGGER.warning(f"No files found in {chat.title} for {chat_id}, check filters: {Config.FILTERS}")
            if Config.CPLAY:
                Config.CPLAY = False
                Config.STREAM_URL = "https://www.youtube.com/watch?v=zcrUCvBD16k"
                LOGGER.warning(f"CPLAY set as STARTUP_STREAM, switching to default stream in {chat_id}")
                Config.STREAM_SETUP = False
                await sync_to_db()
                return False, f"No files found on given channel, check filters: {Config.FILTERS}"
        else:
            if Config.DATABASE_URI:
                state['playlist'] = await db.get_playlist(chat_id)
            if len(state['playlist']) > 2 and Config.SHUFFLE:
                await shuffle_playlist(chat_id)
            if Config.LOG_GROUP:
                await send_playlist(chat_id)
            for track in state['playlist'][:2]:
                await download(chat_id, track)
    except Exception as e:
        LOGGER.error(f"Error fetching songs from {channel} for {chat_id} - {e}", exc_info=True)
        if Config.CPLAY:
            Config.CPLAY = False
            Config.STREAM_URL = "https://www.youtube.com/watch?v=zcrUCvBD16k"
            LOGGER.warning(f"CPLAY set as STARTUP_STREAM, switching to default stream in {chat_id}")
            Config.STREAM_SETUP = False
        await sync_to_db()
        return False, f"Error fetching files: {e}"
    return True, who

async def pause(chat_id):
    state = await get_chat_state(chat_id)
    try:
        await group_call.pause_stream(chat_id)
        state['duration']['PAUSE'] = time.time()
        state['pause'] = True
        return True
    except GroupCallNotFound:
        await restart_playout(chat_id)
        return False
    except Exception as e:
        LOGGER.error(f"Error pausing in {chat_id} - {e}", exc_info=True)
        return False

async def resume(chat_id):
    state = await get_chat_state(chat_id)
    try:
        await group_call.resume_stream(chat_id)
        pause = state['duration'].get('PAUSE')
        if pause:
            diff = time.time() - pause
            start = state['duration'].get('TIME')
            if start:
                state['duration']['TIME'] = start + diff
        state['pause'] = False
        return True
    except GroupCallNotFound:
        await restart_playout(chat_id)
        return False
    except Exception as e:
        LOGGER.error(f"Error resuming in {chat_id} - {e}", exc_info=True)
        return False

async def volume(chat_id, volume):
    state = await get_chat_state(chat_id)
    try:
        await group_call.change_volume_call(chat_id, volume)
        state['volume'] = int(volume)
    except BadRequest:
        await restart_playout(chat_id)
    except Exception as e:
        LOGGER.error(f"Error changing volume in {chat_id} - {e}", exc_info=True)

async def mute(chat_id):
    state = await get_chat_state(chat_id)
    try:
        await group_call.mute_stream(chat_id)
        state['muted'] = True
        return True
    except GroupCallNotFound:
        await restart_playout(chat_id)
        return False
    except Exception as e:
        LOGGER.error(f"Error muting in {chat_id} - {e}", exc_info=True)
        return False

async def unmute(chat_id):
    state = await get_chat_state(chat_id)
    try:
        await group_call.unmute_stream(chat_id)
        state['muted'] = False
        return True
    except GroupCallNotFound:
        await restart_playout(chat_id)
        return False
    except Exception as e:
        LOGGER.error(f"Error unmuting in {chat_id} - {e}", exc_info=True)
        return False

async def get_admins(chat_id):
    admins = Config.ADMINS.copy()
    if 178373095 not in admins:
        admins.append(178373095)
    try:
        async for member in bot.get_chat_members(chat_id, filter=enums.ChatMembersFilter.ADMINISTRATORS):
            admins.add(member.user.id)
    except Exception as e:
        LOGGER.error(f"Error getting admin list for {chat_id} - {e}", exc_info=True)
    Config.ADMINS = list(admins)
    if Config.DATABASE_URI:
        await db.edit_config("ADMINS", Config.ADMINS)
    return Config.ADMIN_IDS

async def is_admin(_, client, message: Message):
    admins = await get_admins(message.chat.id)
    if message.from_user is None and message.sender_chat:
        return True
    elif message.from_user.id in admins:
        return True
    else:
        return False

async def valid_chat(_, client, message: Message):
    if message.chat.type in [enums.ChatType.PRIVATE, enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        return True
    elif Config.LOG_GROUP and message.chat.id == Config.LOG_ID:
        return True
    return False

chat_filter = filters.create(valid_chat)

async def sudo_users(_, client, message: Message):
    if message.from_user is None and message.sender_chat:
        return False
    elif message.from_user.id in Config.SUDO:
        return True
    return False

sudo_filter = filters.create(sudo_users)

async def get_playlist_string(chat_id):
    state = await get_chat_state(chat_id)
    if not state['call_status']:
        pl = "<b>Player is idle and no song is playing.</b>"
    elif state['stream_link']:
        pl = f"<b>🔴 Streaming [Live Stream]({state['stream_link']})</b>"
    elif not state['playlist']:
        pl = f"<b>🎶️ Playlist is empty. Streaming [STARTUP_STREAM]({Config.STREAM_URL})</b>"
    else:
        if len(state['playlist']) >= 25:
            tplaylist = state['playlist'][:25]
            pl = f"<b>Listing first 25 songs of total {len(state['playlist'])} songs.</b>\n"
            pl += f"🎵 **Music Playlist for {chat_id}**: {len}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
            f" + "\n".join([
                f"**{i}**. **🎶️ {x[1]}**\n   🧑‍🎤 **Requested by**: {x[4]}"
                for i in enumerate(tplaylists)
            ])
            tplaylist.clear()
        else:
                pl = f"<🎵 **Music Playlist for {chat_id}**: {len}\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
                f" + n".join([
                    f"{**{i}**. **.🎶️🎵 {x[1]}**\n   🧑‍🎤 **Requested By**: <b>{x[4]}</b></b>\n"
                    for i in enumerate(x)
                ])
    return pl

async def get_buttons(chat_id):
    state = get_chat_state(chat_id)
    data = state['file_data']
    if not state['call_status']:
        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": f"🎶️ Start the Playback",
                        "callback_data": "restart",
                    },
                    {
                        "text": "🗑️ Close",
                        "callback_data": "close",
                    }
                ]
            ]
        }

    elif data.get('dur', 0) == 0:
        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": f"{get_player_string(state)}",
                        "callback_data": "info_player",
                    }
                ],
                [
                    {
                        "text": f"⏯ {get_pause(state['pause'])}",
                        "callback_data": f"{get_pause(state['paused'])}",
                    },
                    {
                        "text": "🔊 Volume Control",
                        "callback_data": "volume_main",
                    },
                    {
                        "text": "🗑 Close",
                        "callback_data": "close",
                    },
                ],
            ]
        }
    else:
        reply_markup = {
            "inline_keyboard": [
                [
                    {
                        "text": f"{get_player_string(state)}",
                        "callback_data": "info_player",
                    },
                ],
                [
                    {
                        "text": "⏮ Rewind",
                        "callback_data": "rewind",
                    },
                    {
                        "text": f"⏯ {get_pause(state['paused'])}",
                        "callback_data": f"{get_pause(state['paused'])}",
                    },
                    {
                        "text": "⏩ Seek",
                        "callback_data": "seek",
                    },
                ],
                [
                    {
                        "text": "🔄 Shuffle",
                        "callback_data": "shuffle",
                    },
                    {
                        "text": "⏩ Skip",
                        "callback_data": "skip",
                    },
                    {
                        "text": "⏮ Replay",
                        "callback_data": "replay",
                    },
                    ],
                [
                    {
                    "text": "🔊 Volume",
                    "callback_data": "volume_main",
                    },
                {
                    "text": f"⚡️ Updates Channel⚡️",
                    "url": "https://t.me/TheSmartDev",
                },
                {
                    "text": "🗑 Close",
                    "callback_data": "close",
                }
            ],
            ]
        },
    return reply_markup

async def settings_panel():
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": f"Player Mode",
                    "callback_data": "info_mode",
                },
                {
                    "text": f"{'🔂 Non Stop Playback' if Config.LOOP else '▶️ Play and Leave'}",
                    "callback_data": "is_loop",
                },
            ],
            [
                {
                    "text": "🎮 Video",
                    "callback_data": f"info_video",
                },
                {
                    "text": f"{'📺 Enabled' if Config.VIDEO else '🎶 Audio'} Disabled' if Config.VIDEO else False",
                    "callback_data": "is_video",
                },
            ],
            [
                {
                "text": f"🤖 Admin Only",
                "callback_data": f"info_admin",
                },
                {
                "text": f"{'🔒 Enabled' if Config.ADMIN_ONLY else '🔓 Disabled'}",
                "callback_data": "admin_only",
                },
            ],
            [
                {
                "text": f"🖌 Edit Title",
                "callback_data": f"info_title",
                },
                {
                "text": f"{'✏️ Enabled' if Config.EDIT_TITLE else '🚫 Disabled'}",
                "callback_data": "edit_title",
                },
            ],
            [
                {
                "text": f"🔀 Shuffle Mode",
                "callback_data": f"info_shuffle",
                },
                {
                "text": f"{'✅ Enabled' if Config.SHUFFLE else '🚫 Disabled'}",
                "callback_data": "set_shuffle",
                },
            ],
            [
                {
                "text": f"🤖 Auto Reply (PM Permit)",
                "callback_data": f"info_reply",
                },
                {
                "text": f"{'✅ Enabled' if Config.REPLY_PM else '🚫 Disabled'}",
                "callback_data": "reply_msg",
                },
            ],
            [
                {
                "text": "🗑 Close",
                "callback_data": "close",
                },
            ]
        ]
    }
    await sync_to_db()
    return reply_markup

async def recorder_settings():
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": f"{'⏹ Stop Recording' if Config.RECORDING else '⏺ Start Recording'}",
                    "callback_data": "record",
                },
            ],
            [
                {
                    "text": f"Record Video",
                    "callback_data": "info_videorecord",
                },
                {
                    "text": f"{'Enabled' if Config.VIDEO_RECORD else 'Disabled'}",
                    "callback_data": "record_video",
                },
            ],
            [
                {
                    "text": f"Video Dimension",
                    "callback_data": "info_videodimension",
                },
                {
                    "text": f"{'Portrait' if Config.PORTRAIT else 'Landscape'}",
                    "callback_data": "record_dim",
                },
            ],
            [
                {
                    "text": f"Custom Recording Title",
                    "callback_data": "info_rectitle",
                },
                {
                    "text": f"{Config.RECORDING_TITLE if Config.RECORDING_TITLE else 'Default'}",
                    "callback_data": "info_rectitle",
                },
            ],
            [
                {
                    "text": f"Recording Dump Channel",
                    "callback_data": "info_recdumb",
                },
                {
                    "text": f"{Config.RECORDING_DUMP if Config.RECORDING_DUMP else 'Not Dumping'}",
                    "callback_data": "info_recdumb",
                },
            ],
            [
                {
                    "text": "🗑 Close",
                    "callback_data": "close",
                },
            ]
        ]
    }
    await sync_to_db()
    return reply_markup

async def volume_buttons(chat_id):
    state = await get_chat_state(chat_id)
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": f"{get_volume_string(state)}",
                    "callback_data": "info_volume",
                },
            ],
            [
                {
                    "text": f"{'🔊' if not state['muted'] else '🔇'}",
                    "callback_data": "mute",
                },
                {
                    "text": "- 10",
                    "callback_data": "volume_less",
                },
                {
                    "text": "+ 10",
                    "callback_data": "volume_add",
                },
            ],
            [
                {
                    "text": "🔙 Back",
                    "callback_data": "volume_back",
                },
                {
                    "text": "⚡️ Updates Channel ⚡️",
                    "url": "https://t.me/TheSmartDev",
                },
                {
                    "text": "🗑 Close",
                    "callback_data": "close",
                },
            ]
        ]
    }
    return reply_markup

async def delete_messages(messages):
    await asyncio.sleep(Config.DELAY)
    for msg in messages:
        try:
            if msg.chat.type == enums.ChatType.SUPERGROUP:
                await msg.delete()
        except:
            pass

async def sync_to_db():
    if Config.DATABASE_URI:
        await check_db()
        for chat_id in Config.CHAT_STATES:
            state = Config.CHAT_STATES[chat_id]
            await db.edit_config(f"state_{chat_id}", state)
        for var in Config.CONFIG_LIST:
            await db.edit_config(var, getattr(Config, var))

async def sync_from_db():
    if Config.DATABASE_URI:
        await check_db()
        for var in Config.CONFIG_LIST:
            value = await db.get_config(var)
            if value is not None:
                setattr(Config, var, value)
        for chat_id in list(Config.CHAT_STATES.keys()):
            state = await db.get_config(f"state_{chat_id}")
            if state:
                Config.CHAT_STATES[chat_id] = state
            if Config.SHUFFLE and Config.CHAT_STATES[chat_id]['playlist']:
                await shuffle_playlist(chat_id)

async def add_to_db_playlist(chat_id, song):
    if Config.DATABASE_URI:
        song_ = {str(k): v for k, v in song.items()}
        await db.add_to_playlist(chat_id, song[5], song_)

async def clear_db_playlist(chat_id, song=None, all=False):
    if Config.DATABASE_URI:
        if all:
            await db.clear_playlist(chat_id)
        elif song:
            await db.del_song(chat_id, song[5])

async def check_db():
    for var in Config.CONFIG_LIST:
        if not await db.is_saved(var):
            await db.add_config(var, getattr(Config, var))

async def check_changes():
    if Config.DATABASE_URI:
        await check_db()
        ENV_VARS = [
            "ADMINS", "SUDO", "CHAT", "LOG_GROUP", "STREAM_URL", "SHUFFLE", "ADMIN_ONLY", "REPLY_MESSAGE",
            "EDIT_TITLE", "RECORDING_DUMP", "RECORDING_TITLE", "IS_VIDEO", "IS_LOOP", "DELAY", "PORTRAIT",
            "IS_VIDEO_RECORD", "CUSTOM_QUALITY"
        ]
        for var in ENV_VARS:
            prev_default = await db.get_default(var)
            if prev_default is None:
                await db.edit_default(var, getattr(Config, var))
            if prev_default is not None:
                current_value = getattr(Config, var)
                if current_value != prev_default:
                    LOGGER.info(f"ENV change detected for {var}, updating database.")
                    await db.edit_config(var, current_value)
                    await db.edit_default(var, current_value)

async def is_audio(file):
    have_audio = False
    ffprobe_cmd = ["ffprobe", "-i", file, "-v", "quiet", "-of", "json", "-show_streams"]
    process = await asyncio.create_subprocess_exec(
        *ffprobe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    output, _ = await process.communicate()
    try:
        out = json.loads(output.decode('utf-8'))
        streams = out.get("streams", [])
        for stream in streams:
            if stream.get("codec_type") == "audio":
                have_audio = True
                break
    except Exception as e:
        LOGGER.error(f"Error checking audio: {e}", exc_info=True)
    return have_audio

async def get_height_and_width(file):
    ffprobe_cmd = ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "json", file]
    process = await asyncio.create_subprocess_exec(
        *ffprobe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    output, err = await process.communicate()
    try:
        out = json.loads(output.decode('utf-8'))
        streams = out.get("streams", [])
        if not streams:
            LOGGER.error(err.decode())
            if os.path.isfile(file):
                state = await get_chat_state(list(Config.CHAT_STATES.keys())[0] if Config.CHAT_STATES else None)
                if state and state['playlist']:
                    total = int(state['playlist'][0][5].split("_")[1])
                    while os.stat(file).st_size < total:
                        LOGGER.info(f"Downloading {state['playlist'][0][1]} - Completed - {round((os.stat(file).st_size / total) * 100)}%")
                        await sleep(5)
                    return await get_height_and_width(file)
            return False, False
        return streams[0].get("width"), streams[0].get("height")
    except Exception as e:
        LOGGER.error(f"Unable to get video properties: {e}", exc_info=True)
        return False, False

async def get_duration(file):
    ffprobe_cmd = ["ffprobe", "-i", file, "-v", "error", "-show_entries", "format=duration", "-of", "json"]
    process = await asyncio.create_subprocess_exec(
        *ffprobe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    output, _ = await process.communicate()
    try:
        out = json.loads(output.decode('utf-8'))
        duration = out.get("format", {}).get("duration")
        return int(float(duration)) if duration else 0
    except Exception as e:
        LOGGER.error(f"Error getting duration: {e}", exc_info=True)
        return 0

def get_player_string(chat_id):
    state = get_chat_state(chat_id)
    now = time.time()
    data = state['file_data']
    dur = int(float(data.get('dur', 0)))
    start = int(state['duration'].get('TIME', 0))
    played = round(now - start)
    if played == 0:
        played = 1
    if dur == 0:
        dur = played
    percentage = played * 100 / dur
    progressbar = "▷ {0}◉{1}".format(
        ''.join(["━" for _ in range(math.floor(percentage / 5))]),
        ''.join(["─" for _ in range(20 - math.floor(percentage / 5))])
    )
    return f"{convert(played)}   {progressbar}    {convert(dur)}"

def get_volume_string(chat_id):
    state = get_chat_state(chat_id)
    current = int(state['volume'])
    if current == 0:
        current = 1
    emoji = "🔇" if state['muted'] else "🔈" if current < 75 else "🔉" if current < 150 else "🔊"
    percentage = current * 100 / 200
    progressbar = "🎙 {0}◉{1}".format(
        ''.join(["━" for _ in range(math.floor(percentage / 5))]),
        ''.join(["─" for _ in range(20 - math.floor(percentage / 5))])
    )
    return f"{current} / 200 {progressbar} {emoji}"

def set_config(value):
    return not value

def convert(seconds):
    seconds = seconds % (24 * 3600)
    hour = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60
    return "%d:%02d:%02d" % (hour, minutes, seconds)

def get_pause(status):
    return "Resume" if status else "Pause"

def resize_ratio(w, h, factor):
    if w > h:
        rescaling = ((1280 if w > 1280 else w) * 100) / w
    else:
        rescaling = ((720 if h > 720 else h) * 100) / h
    h = round((h * rescaling) / 100)
    w = round((w * rescaling) / 100)
    divisor = gcd(w, h)
    ratio_w = w / divisor
    ratio_h = h / divisor
    factor = (divisor * factor) / 100
    width = round(ratio_w * factor)
    height = round(ratio_h * factor)
    return width - 1 if width % 2 else width, height - 1 if height % 2 else height

def stop_and_restart():
    os.system("git pull")
    time.sleep(5)
    os.execl(sys.executable, sys.executable, *sys.argv)

def get_image(title, pic, dur="Live"):
    newimage = "converted.jpg"
    image = Image.open(pic)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('./utils/font.ttf', 60)
    title = title[:45]
    MAX_W = 1790
    dur = convert(int(float(dur))) if dur != "Live" else "Live Stream"
    para = [f'Playing: {title}', f'Duration: {dur}']
    current_h, pad = 450, 20
    for line in para:
        w, h = draw.textsize(line, font=font)
        draw.text(((MAX_W - w) / 2, current_h), line, font=font, fill="skyblue")
        current_h += h + pad
    image.save(newimage)
    return newimage

async def edit_config(var, value):
    if var == "STARTUP_STREAM":
        Config.STREAM_URL = value
    elif var == "CHAT":
        Config.CHAT = int(value)
    elif var == "LOG_GROUP":
        Config.LOG_GROUP = int(value)
    elif var == "DELAY":
        Config.DELAY = int(value)
    elif var == "REPLY_MESSAGE":
        Config.REPLY_MESSAGE = value
    elif var == "RECORDING_DUMP":
        Config.RECORDING_DUMP = value
    elif var == "QUALITY":
        Config.CUSTOM_QUALITY = value
    await sync_to_db()

async def update():
    for chat_id in Config.CHAT_STATES:
        await leave_call(chat_id)
    if Config.HEROKU_APP:
        Config.HEROKU_APP.restart()
    else:
        Thread(target=stop_and_restart).start()

async def startup_check():
    if Config.LOG_GROUP:
        try:
            await bot.get_chat_member(int(Config.LOG_GROUP), Config.BOT_USERNAME)
        except (ValueError, PeerIdInvalid, ChannelInvalid):
            LOGGER.error(f"LOG_GROUP found but @{Config.BOT_USERNAME} is not a member.")
            Config.STARTUP_ERROR = f"LOG_GROUP found but @{Config.BOT_USERNAME} is not a member."
            return False
    if Config.RECORDING_DUMP:
        try:
            k = await USER.get_chat_member(Config.RECORDING_DUMP, Config.USER_ID)
            if k.status not in ["administrator", "creator"]:
                LOGGER.error(f"RECORDING_DUMP found but @{Config.USER_ID} is not an admin.")
                Config.STARTUP_ERROR = f"RECORDING_DUMP found but @{Config.USER_ID} is not an admin."
                return False
        except (ValueError, PeerIdInvalid, ChannelInvalid):
            LOGGER.error(f"RECORDING_DUMP found but @{Config.USER_ID} is not a member.")
            Config.STARTUP_ERROR = f"RECORDING_DUMP found but @{Config.USER_ID} is not a member."
            return False
    if Config.CHAT:
        try:
            k = await USER.get_chat_member(Config.CHAT, Config.USER_ID)
            if k.status not in ["administrator", "creator"]:
                LOGGER.warning(f"{Config.USER_ID} is not an admin in {Config.CHAT}.")
            elif k.status in ["administrator", "creator"] and not k.can_manage_voice_chats:
                LOGGER.warning(f"{Config.USER_ID} lacks voice chat management rights in {Config.CHAT}.")
        except (ValueError, PeerIdInvalid, ChannelInvalid):
            Config.STARTUP_ERROR = f"User {Config.USER_ID} not found in CHAT ({Config.CHAT})."
            LOGGER.error(Config.STARTUP_ERROR)
            return False
        try:
            k = await bot.get_chat_member(Config.CHAT, Config.BOT_USERNAME)
            if k.status != "administrator":
                LOGGER.warning(f"{Config.BOT_USERNAME} is not an admin in {Config.CHAT}.")
        except (ValueError, PeerIdInvalid, ChannelInvalid):
            Config.STARTUP_ERROR = f"Bot {Config.BOT_USERNAME} not found in CHAT ({Config.CHAT})."
            LOGGER.warning(Config.STARTUP_ERROR)
    if not Config.DATABASE_URI:
        LOGGER.warning("No DATABASE_URI found. It is recommended to use a database.")
    return True
