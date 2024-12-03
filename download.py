#!/usr/bin/env python3
# YouTube Music Playlist Downloader
version = "1.0"

import os
import re
import sys
import copy
import json
import time
import requests
import subprocess
import concurrent.futures
from colorama import Back, Fore, Style, init
from PIL import Image
from io import BytesIO
from pathlib import Path
from langcodes import Language
from yt_dlp import YoutubeDL, postprocessor
from urllib.parse import urlparse, parse_qs
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TRCK, TALB, TDRC, WOAR, SYLT, USLT, error

# ID3 info:
# APIC: thumbnail
# TIT2: title
# TPE1: artist
# TRCK: track number
# TALB: album
# TDRC: upload date
# WOAR: link
# SYLT: synced lyrics
# USLT: unsynced lyrics

class FilePathCollector(postprocessor.common.PostProcessor):
    def __init__(self):
        super(FilePathCollector, self).__init__(None)
        self.file_paths = []

    def run(self, information):
        self.file_paths.append(information['filepath'])
        return [], information

class SongFileInfo:
    def __init__(self, video_id, name, file_name, file_path, track_num):
        self.video_id = video_id
        self.name = name
        self.file_name = file_name
        self.file_path = file_path
        self.track_num = track_num

def write_config(file, config: dict):
    with open(file, "w") as f:
        json.dump(config, f, indent=4)

def check_ffmpeg():
    ffmpeg_available = True
    try:
        subprocess.check_output(['ffmpeg', '-version'])
    except Exception as e:
        ffmpeg_available = False
    if not ffmpeg_available:
        print("\n".join([
            f"{Fore.RED}[ERROR] ffmpeg não encontrado. Certifique-se de que o ffmpeg esteja instalado",
            "e que você o incluiu em sua variável de ambiente PATH.",
            "Faça o download do ffmpeg aqui: https://www.ffmpeg.org/download.html.",
            "-----------------------------------------------------------",
        ]))
    return ffmpeg_available

def get_playlist_info(config: dict):
    ytdl_opts = {
        "quiet": True,
        "geo_bypass": True,
        "dump_single_json": True,
        "extract_flat": True,
        "cookiefile": None if config["cookie_file"] == "" else config["cookie_file"],
        "cookiesfrombrowser": None if config["cookies_from_browser"] == "" else tuple(config["cookies_from_browser"].split(":")),
        "playlistreverse": config["reverse_playlist"]
    }
    with YoutubeDL(ytdl_opts) as ytdl:
        info_dict = ytdl.extract_info(config["url"], download=False)

    return info_dict

def convert_image_type(image, image_type):
    with BytesIO() as f:
        image.convert("RGB").save(f, format=image_type)
        return f.getvalue()

def update_track_num(file_path, track_num):
    tags = ID3(file_path)
    tags.add(TRCK(encoding=3, text=str(track_num)))
    tags.save(v2_version=3)

def update_file_order(playlist_name, song_file_info, track_num, config: dict, missing_video: bool):
    # Fix name if mismatching
    if config["track_num_in_name"]:
        song_file_name = re.sub(r"^[0-9]+. ", "", song_file_info.file_name)
        file_name = f"{track_num}. {song_file_name}"
    else:
        file_name = song_file_info.file_name
    file_path = os.path.join(playlist_name, file_name)
            
    # Update song index if not matched
    if song_file_info.track_num != track_num and config["include_metadata"]["track"]:
        if missing_video:
            print(f"{Fore.BLUE}Reordenando '{song_file_info.name}' da posição {song_file_info.track_num} para {track_num} devido à falta do link do vídeo...")
        else:
            print(f"{Fore.BLUE}Reordenando '{song_file_info.name}' da posiçao {song_file_info.track_num} para {track_num}...")
        update_track_num(song_file_info.file_path, track_num)

    if song_file_info.file_path != file_path:
        if song_file_info.track_num == track_num:
            # Track num in name was incorrectly modified manually by user
            print(f"{Fore.BLUE}Renomeando nome de arquivo incorreto de '{song_file_info.file_name}' para '{file_name}'")
        os.rename(song_file_info.file_path, file_path)

    return file_path

def get_metadata_map():
    return {
        "title": ["TIT2"],
        "cover": ["APIC:Front cover"],
        "track": ["TRCK"],
        "artist": ["TPE1"],
        "album": ["TALB"],
        "date": ["TDRC"],
        "url": ["WOAR"],
        "lyrics": ["SYLT", "USLT"]
    }

def flatten(l):
    return [item for sublist in l for item in sublist]

def get_metadata_dict(tags):
    return {tag:tags.getall(tag) for tag in flatten(get_metadata_map().values())}

def valid_metadata(config: dict, metadata_dict: dict):
    include_metadata = config["include_metadata"].copy()

    # WOAR URL is required to identify video
    include_metadata["url"] = True

    selected_tags = flatten([value for key, value in get_metadata_map().items() if include_metadata[key]])
    return all([value for tag, value in metadata_dict.items() if tag in selected_tags])

def get_song_info_ytdl(track_num, config: dict):
    # Get ytdl for song info
    name_format = config["name_format"]
    if config["track_num_in_name"]:
        name_format = f"{track_num}. {name_format}"

    ytdl_opts = {
        "quiet": True,
        "geo_bypass": True,
        "outtmpl": name_format,
        "format": config["audio_format"],
        "cookiefile": None if config["cookie_file"] == "" else config["cookie_file"],
        "cookiesfrombrowser": None if config["cookies_from_browser"] == "" else tuple(config["cookies_from_browser"].split(":")),
        "writesubtitles": True,
        "allsubtitles": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": config["audio_codec"],
            "preferredquality": config["audio_quality"],
        }]
    }

    info_dict = {}
    return YoutubeDL(ytdl_opts)

def get_song_info(track_num, link, config: dict):
    # Get song metadata from youtube
    ytdl = get_song_info_ytdl(track_num, config)
    return ytdl.extract_info(link, download=False)

def get_subtitles_url(subtitles, lang):
    return next(sub for sub in subtitles[lang] if sub["ext"] == "json3")["url"]

def generate_metadata(file_path, link, track_num, playlist_name, config: dict, regenerate_metadata: bool, force_update: bool):
    try:
        tags = ID3(file_path)
    except:
        # Unsupported audio codec for metadata
        force_update_file_name = ""
        if force_update:
            try:
                info_dict = get_song_info(track_num, link, config)
                info_dict_with_audio_ext = dict(info_dict)
                info_dict_with_audio_ext["ext"] = config["audio_codec"]
                force_update_file_name = get_song_info_ytdl(track_num, config).prepare_filename(info_dict_with_audio_ext)
            except Exception as e:
                raise Exception(f"{Fore.RED}Falha ao obter informações sobre o nome do arquivo atualizado - {e}")
        return force_update_file_name

    # Generate only if metadata is missing or if explicitly flagged
    metadata_dict = get_metadata_dict(tags)

    force_update_file_name = ""
    if force_update:
        for tag in metadata_dict.keys():
            if tag != "WOAR":
                # WOAR URL is required to identify video
                tags.delall(tag)
                metadata_dict[tag] = []

    if regenerate_metadata or force_update or not valid_metadata(config, metadata_dict):
        try:
            info_dict = get_song_info(track_num, link, config)

            if force_update:
                info_dict_with_audio_ext = dict(info_dict)
                info_dict_with_audio_ext["ext"] = config["audio_codec"]
                force_update_file_name = get_song_info_ytdl(track_num, config).prepare_filename(info_dict_with_audio_ext)

            thumbnail = info_dict.get("thumbnail")
            upload_date = info_dict.get("upload_date")
            title = info_dict.get("title")
            track = info_dict.get("track")
            uploader = info_dict.get("uploader")
            artist = info_dict.get("artist")
            album = info_dict.get("album")
            subtitles = info_dict.get("subtitles")
            requested_subtitles = info_dict.get("requested_subtitles")
        except Exception as e:
            raise Exception(f"{Fore.RED}Falha ao obter informações - {e}")

        try:
            # Generate tags
            print(f"{Fore.BLUE}Atualizando metadados para '{title}'...")
            include_metadata = config["include_metadata"]

            # These tags will not be regenerated in case of config changes
            if not metadata_dict["APIC:Front cover"] and include_metadata["cover"]:
                # Generate thumbnail
                img = Image.open(requests.get(thumbnail, stream=True).raw)

                # Ensure aspect ratio
                target_ratio = [16, 9]
                width, height = img.size
                width_ratio = width / target_ratio[0]
                height_ratio = height / target_ratio[1]
                if width_ratio > height_ratio:
                    half_width = width / 2
                    min_offset = (height_ratio * target_ratio[0]) / 2
                    left = half_width - min_offset
                    right = half_width + min_offset
                    img = img.crop([left, 0, right, height])
                elif height_ratio > width_ratio:
                    half_height = height / 2
                    min_offset = (width_ratio * target_ratio[1]) / 2
                    top = half_height - min_offset
                    bottom = half_height + min_offset
                    img = img.crop([0, top, width, bottom])

                # Crop to square
                width, height = img.size
                half_width = width / 2
                half_height = height / 2
                min_offset = min(half_width, half_height)
                left = half_width - min_offset
                right = half_width + min_offset
                top = half_height - min_offset
                bottom = half_height + min_offset
                img_data = convert_image_type(img.crop([left, top, right, bottom]), config["image_format"])
                tags.add(APIC(3, f"image/{config['image_format']}", 3, "Front cover", img_data))

            if not metadata_dict["TRCK"] and include_metadata["track"]:
                tags.add(TRCK(encoding=3, text=str(track_num)))

            if not metadata_dict["TDRC"] and include_metadata["date"]:
                tags.add(TDRC(encoding=3, text=time.strftime('%Y-%m-%d', time.strptime(upload_date, '%Y%m%d'))))

            if not metadata_dict["WOAR"]:
                tags.add(WOAR(link))

            if include_metadata["lyrics"] and (not metadata_dict["SYLT"] or not metadata_dict["USLT"]):
                synced_lyrics = []
                unsynced_lyrics = []
                lang = "pt-BR"
                lyrics_langs = config["lyrics_langs"]
                strict_lang_match = config["strict_lang_match"]

                # Filter out subtitles related to live chat
                if requested_subtitles is not None:
                    requested_subtitles = {key:value for (key, value) in requested_subtitles.items() if not key.startswith("live")}

                if subtitles and requested_subtitles and len(subtitles) > 0:
                    subtitles_url = None
                    try:
                        if len(lyrics_langs) == 0:
                            lang = next(iter(requested_subtitles))
                            subtitles_url = get_subtitles_url(subtitles, lang)
                            print(f"{Fore.YELLOW}Selecionando o primeiro idioma disponível para letras: {lang}")
                        else:
                            lyrics_found = False
                            for lyrics_lang in lyrics_langs:
                                for requested_lang in requested_subtitles.keys():
                                    # Regex match full string
                                    if re.match(r"^" + lyrics_lang + r"$", requested_lang):
                                        subtitles_url = get_subtitles_url(subtitles, requested_lang)
                                        lang = requested_lang
                                        print(f"{Fore.YELLOW}Idioma selecionado para letras: {lang}")
                                        lyrics_found = True
                                        break
                                if lyrics_found:
                                    break

                            if subtitles_url is None:
                                available_languages_str = str(list(requested_subtitles.keys()))
                                print(f"{Fore.YELLOW}Letras indisponíveis para idiomas selecionados. Idiomas disponíveis: {available_languages_str}")
                                if not strict_lang_match:
                                    lang = next(iter(requested_subtitles))
                                    subtitles_url = get_subtitles_url(subtitles, lang)
                                    print(f"{Fore.YELLOW}Selecionando o primeiro idioma disponível para letras: {lang}")
                    except:
                        subtitles_url = None

                    if subtitles_url is not None:
                        try:
                            content = json.loads(requests.get(subtitles_url, stream=True).text)

                            last_timestamp = -1
                            last_lines = []

                            for event in content["events"]:
                                timestamp = event["tStartMs"]
                                line = ""
                                for seg in event["segs"]:
                                    line += seg["utf8"]
                                # Remove invalid characters
                                line = line.replace("\u200b", "").replace("\u200c", "")

                                if (timestamp - last_timestamp) < 1000 and line.strip() in last_lines:
                                    # Skip if line is repeated too quickly
                                    last_timestamp = timestamp
                                    continue

                                if timestamp == last_timestamp:
                                    # Append line into previous line if same timestamp has multiple lines
                                    lyrics_line = list(synced_lyrics[-1])
                                    lyrics_line[0] += "\n" + line
                                    synced_lyrics[-1] = tuple(lyrics_line)

                                    unsynced_lyrics[-1] += "\n" + line

                                    last_lines.append(line.strip())
                                else:
                                    synced_lyrics.append((line, timestamp))
                                    unsynced_lyrics.append(line)
                                    last_lines = [line.strip()]
                                last_timestamp = timestamp
                        except Exception as e:
                            print(f"{Fore.RED}Não foi possível obter a letra: {e}")

                try:
                    lang = Language.get(lang).to_alpha3()
                except:
                    print(f"{Fore.YELLOW}Salvando o idioma das letras não reconhecido '{lang}' as 'pt-BR'")
                    lang = Language.get("pt-BR").to_alpha3()

                if len(synced_lyrics) == 0:
                    synced_lyrics = [(Fore.RED + "Letras indisponíveis", 0)]
                if len(unsynced_lyrics) == 0:
                    unsynced_lyrics = [Fore.RED + "Letras indisponíveis"]

                tags.add(SYLT(encoding=3, lang=lang, format=2, type=1, text=synced_lyrics))
                tags.add(USLT(encoding=3, lang=lang, text="\n".join(unsynced_lyrics)))

            # These tags can be regenerated in case of config changes
            if include_metadata["title"]:
                if config["use_title"] or track is None:
                    tags.add(TIT2(encoding=3, text=title))
                else:
                    tags.add(TIT2(encoding=3, text=track))

            if include_metadata["artist"]:
                if config["use_uploader"] or artist is None:
                    tags.add(TPE1(encoding=3, text=uploader))
                else:
                    tags.add(TPE1(encoding=3, text=artist))

            if include_metadata["album"]:
                if config["use_playlist_name"]:
                    tags.add(TALB(encoding=3, text=playlist_name))
                elif album is not None:
                    tags.add(TALB(encoding=3, text=album))
                else:
                    tags.add(TALB(encoding=3, text="Unknown Album"))

            tags.save(v2_version=3)
        except Exception as e:
            raise Exception(f"{Fore.RED}Não foi possível atualizar os metadados da música: {e}")

    return force_update_file_name

def download_song(link, playlist_name, track_num, config: dict, is_playlist=True):
    if is_playlist:
        directory = os.path.join(os.getcwd(), "playlist-download", playlist_name)
    else:
        directory = os.path.join(os.getcwd(), "music-download")

    name_format = config["name_format"]
    if config["track_num_in_name"]:
        name_format = f"{track_num}. {name_format}"

    ytdl_opts = {
        "outtmpl": f"{directory}/{name_format}",
        "ignoreerrors": True,
        "format": config["audio_format"],
        "cookiefile": None if config["cookie_file"] == "" else config["cookie_file"],
        "cookiesfrombrowser": None if config["cookies_from_browser"] == "" else tuple(config["cookies_from_browser"].split(":")),
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": config["audio_codec"],
            "preferredquality": config["audio_quality"],
        }],
        "geo_bypass": True
    }

    if not config["verbose"]:
        ytdl_opts["quiet"] = True
        ytdl_opts["external_downloader_args"] = ["-loglevel", "panic"]

    with YoutubeDL(ytdl_opts) as ytdl:
        file_path_collector = FilePathCollector()
        ytdl.add_post_processor(file_path_collector)
        result = ytdl.download([link])
        if len(file_path_collector.file_paths) == 0:
            raise Exception(Fore.CYAN + "Nenhum caminho de download de arquivo encontrado, o vídeo pode estar indisponível")
        file_path = file_path_collector.file_paths[0]

    return result, file_path

def download_single_song(url, output_folder):
    ytdl_opts = {
        "outtmpl": f"{output_folder}/%(title)s-%(id)s.%(ext)s",
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "quiet": True
    }

    with YoutubeDL(ytdl_opts) as ytdl:
        ytdl.download([url])

def download_song_and_update(video_info, playlist, link, playlist_name, track_num, config: dict):
    file_path = None
    try:
        result, file_path = download_song(link, playlist_name, track_num, config)

        # Check download failed and video is unavailable
        if result != 0 and video_info["channel_id"] is None:
            # Video title indicates availability of video such as '[Private Video]'
            raise Exception(f"{Fore.RED}O vídeo não está disponível - {video_info['title']}")

        generate_metadata(file_path, link, track_num, playlist["title"], config, False, False)
    except Exception as e:
        error_message = f"{Fore.RED}Não foi possível baixar o número do vídeo {track_num} '{link}': {e}"
        return error_message, track_num
    return None, track_num

def update_song(video_info, song_file_info, file_path, link, track_num, playlist_name, config: dict, regenerate_metadata: bool, force_update: bool):
    # Generate metadata just in case it is missing
    video_unavailable = False
    error_message = []
    try:
        force_update_file_name = generate_metadata(file_path, link, track_num, playlist_name, config, regenerate_metadata, force_update)
        if force_update:
            force_update_file_path = os.path.join(playlist_name, force_update_file_name)
            if file_path != force_update_file_path:
                # Track name needs updating to proper format
                print(f"{Fore.BLUE}Renomeando nome de arquivo incorreto de '{Path(file_path).stem}' para '{Path(force_update_file_path).stem}'")
                os.rename(file_path, force_update_file_path)
    except Exception as e:
        error_message.append(f"{Fore.RED}Não foi possível atualizar metadados para #{track_num} '{link}': {e}")
        if "Este vídeo não está disponivel" in str(e):
            video_unavailable = True

    # Check if video is unavailable
    if video_info["channel_id"] is None or video_unavailable:
        if len(error_message) == 0:
            # Metadata was obtained successfully but some information is missing
            error_message.append(f"{Fore.LIGHTRED_EX}Não foi possível atualizar totalmente os metadados de #{track_num} '{link}'")
        error_text = f"{Fore.LIGHTRED_EX}A música anterior '{song_file_info.name}' não está disponível, mas existe uma cópia local"
        if not video_unavailable and video_info['title'] is not None and video_info['title'] != "":
            # Video title indicates availability of video such as '[Private Video]'
            error_text += f" - {video_info['title']}"
        error_message.append(error_text)

    if len(error_message) > 0:
        return "\n".join(error_message)
    return None

def format_file_name(file_name):
    return re.sub(r"[\\/:*?\"<>|]", "_", file_name)

def get_url_parameter(url, param):
    return parse_qs(urlparse(url).query)[param][0]

def get_video_id_from_metadata(tags):
    links = tags.getall("WOAR")
    if not links or len(links) > 1:
        raise Exception(Fore.RED + "A tag WOAR está em um formato inválido")

    return get_url_parameter(str(links[0]), "v")

def get_song_file_info(playlist_name, song_file_name):
    song_file_path = os.path.join(playlist_name, song_file_name)

    try:
        tags = ID3(song_file_path)
    except:
        # File is not considered a song file if it contains no metadata
        return None

    try:
        song_video_id = get_video_id_from_metadata(tags)
        song_name = tags.get("TIT2", song_file_name)
        song_track_num = int(str(tags.get("TRCK", 0)))
    except Exception as e:
        print(f"{Fore.RED}O arquivo de música '{song_file_name}' está em um formato inválido e será ignorado")
        return None

    return SongFileInfo(song_video_id, song_name, song_file_name, song_file_path, song_track_num)

def get_song_file_infos(playlist_name):
    song_file_infos = {}
    duplicate_files = {}
    for file_name in os.listdir(playlist_name):
        song_file_info = get_song_file_info(playlist_name, file_name)
        if song_file_info is None:
            continue

        if song_file_info.video_id in song_file_infos:
            # Check for duplicate song files
            if song_file_info.video_id not in duplicate_files:
                duplicate_files[song_file_info.video_id] = [song_file_infos[song_file_info.video_id].file_name]

            duplicate_files[song_file_info.video_id].append(song_file_info.file_name)
            continue

        song_file_infos[song_file_info.video_id] = song_file_info

    if duplicate_files:
        exception_strings = []
        for song_video_id, file_names in duplicate_files.items():
            exception_strings.append("\n".join([
                f"{Fore.GREEN}Os arquivos a seguir estão vinculados ao mesmo ID de vídeo '{song_video_id}'",
                "\n".join(["- " + file_name for file_name in file_names])
            ]))

        raise Exception(Fore.LIGHTRED_EX + "\n".join([
            "",
            "===================================================================",
            "[ERROR] Os arquivos a seguir estão vinculados ao mesmo ID de vídeo!",
            "===================================================================",
            "\n\n".join(exception_strings),
            "==============================================================",
            "Remova arquivos de música duplicados para resolver o conflito.",
            "==============================================================",
            ""
        ]))

    return song_file_infos

def setup_include_metadata_config():
    return {key:True for key in get_metadata_map().keys() if key != "url"}

def copy_config(src_config: dict, dst_config: dict):
    # Copy modified src_config values to the dst_config
    for key, value in dst_config.items():
        if isinstance(value, dict):
            sub_dict = {}
            if key in src_config and isinstance(src_config[key], dict):
                sub_dict = src_config[key]

            for sub_key in value:
                if sub_key in sub_dict:
                    value[sub_key] = sub_dict[sub_key]

            dst_config[key] = value
        elif key in src_config and type(dst_config[key]) == type(src_config[key]):
            dst_config[key] = src_config[key]

def get_override_config(video_id, base_config: dict):
    config = copy.deepcopy(base_config)
    if video_id in base_config["overrides"]:
        copy_config(base_config["overrides"][video_id], config)

    return config

def setup_config(config: dict):
    new_config = {
        # Console config options
        "url": "",
        "reverse_playlist": False,

        "use_title": True,
        "use_uploader": True,
        "use_playlist_name": True,

        # File config options
        "sync_folder_name": True,
        "use_threading": True,
        "thread_count": 0,

        "retain_missing_order": False,
        "name_format": "%(title)s-%(id)s.%(ext)s",
        "track_num_in_name": True,
        "audio_format": "bestaudio/best",
        "audio_codec": "mp3",
        "audio_quality": "5",
        "image_format": "jpeg",
        "lyrics_langs": [],
        "strict_lang_match": False,
        "cookie_file": "",
        "cookies_from_browser": "",
        "verbose": False,
        "include_metadata": setup_include_metadata_config()
    }

    # Copy config values to the new config
    copy_config(config, new_config)

    # Create example song config override
    config_copy = copy.deepcopy(new_config)
    excluded_override_keys = ["url", "reverse_playlist", "sync_folder_name", "use_threading", "thread_count", "overrides"]
    for excluded_override_key in excluded_override_keys:
        if excluded_override_key in config_copy:
            config_copy.pop(excluded_override_key)
    new_config["overrides"] = {
        "EXAMPLE_VIDEO_ID_HERE": config_copy
    }

    # Setup individual song config overrides
    if "overrides" in config:
        for key, value in config["overrides"].items():
            if key != "EXAMPLE_VIDEO_ID_HERE" and isinstance(value, dict):
                for excluded_override_key in excluded_override_keys:
                    if excluded_override_key in value:
                        value.pop(excluded_override_key)
                new_config["overrides"][key] = value

    return new_config

def generate_default_config(config: dict, config_file_name: str):
    config = setup_config(config)

    # Get list of links in the playlist
    playlist = get_playlist_info(config)
    playlist_name = format_file_name(playlist["title"])

    # Create playlist folder
    Path(os.path.join("playlist-download", playlist_name)).mkdir(parents=True, exist_ok=True)

    write_config(os.path.join("playlist-download", playlist_name, config_file_name), config)

def generate_playlist(base_config: dict, config_file_name: str, update: bool, force_update: bool, regenerate_metadata: bool, single_playlist: bool, current_playlist_name=None, track_num_to_update=None):
    # Get list of links in the playlist
    playlist = get_playlist_info(base_config)
    
    if "entries" not in playlist:
        raise Exception(f"{Fore.LIGHTRED_EX}Nenhum vídeo encontrado na playlist")
    playlist_entries = playlist["entries"]

    if single_playlist:
        playlist_name = "."
    else:
        playlist_name = format_file_name(playlist["title"])

    # Prepare for downloading
    duplicate_name_index = 1
    adjusted_playlist_name = playlist_name
    while True:
        if single_playlist:
            break

        if duplicate_name_index > 1:
            adjusted_playlist_name = f"{playlist_name} ({duplicate_name_index})"

        if update:
            # Check if playlist name changed
            if current_playlist_name is not None and current_playlist_name != adjusted_playlist_name:
                if not base_config["sync_folder_name"]:
                    adjusted_playlist_name = current_playlist_name
                    break
                try:
                    os.rename(current_playlist_name, adjusted_playlist_name)
                except FileExistsError:
                    duplicate_name_index += 1
                    continue

                print(f"{Fore.LIGHTGREEN_EX}Renomeando a lista de reprodução de '{current_playlist_name}' para '{adjusted_playlist_name}'...")
                if base_config["use_playlist_name"]:
                    # Regenerate metadata to update album tag with playlist name
                    regenerate_metadata = True
        else:
            # Create playlist folder
            try:
                Path(os.path.join("playlist-download", playlist_name)).mkdir(parents=True, exist_ok=True)
            except FileExistsError:
                duplicate_name_index += 1
                continue
        break
    playlist_name = adjusted_playlist_name

    # Update config for playlist
    write_config(os.path.join("playlist-download", playlist_name, config_file_name), base_config)
    song_file_infos = get_song_file_infos(os.path.join("playlist-download", playlist_name)) # May raise exception for duplicate songs
        
    track_num = 1
    skipped_videos = 0
    updated_video_ids = []

    # Insert dummy entries for songs that should retain index order
    for video_id in song_file_infos.keys():
        config = get_override_config(video_id, base_config)
        if config["retain_missing_order"]:
            found = False
            for i, video_info in enumerate(playlist_entries):
                if video_info is not None and video_info["id"] == video_id:
                    found = True
                    break
            if not found:
                # Insert dummy entry
                index = song_file_infos[video_id].track_num - 1
                if index > len(playlist_entries):
                    for i in range(index - len(playlist_entries)):
                        playlist_entries.append(None)
                playlist_entries.insert(index, {"id": video_id, "channel_id": None, "title": None})

    # Prepare threading executor
    download_executor = None
    update_executor = None
    download_futures = []
    update_futures = []
    if base_config["use_threading"]:
        thread_count = base_config["thread_count"]
        if thread_count <= 0:
            thread_count = None
        download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=thread_count)
        update_executor = concurrent.futures.ThreadPoolExecutor(max_workers=thread_count)

    # Download each item in the list
    for i, video_info in enumerate(playlist_entries):
        if video_info is None:
            # Dummy spacer entry to retain index order
            continue

        track_num = i + 1 - skipped_videos
        video_id = video_info["id"]
        link = f"https://www.youtube.com/watch?v={video_id}"
        song_file_info = song_file_infos.get(video_id)

        # Song must be downloaded already and match the current track num when updating a single song
        if track_num_to_update is not None and (song_file_info is None or song_file_info.track_num != track_num_to_update):
            continue

        config = get_override_config(video_id, base_config)
        updated_video_ids.append(video_id)

        # Update metadata for a single song
        if track_num_to_update is not None:
            if song_file_info is not None:
                file_path = os.path.join("playlist-download", playlist_name, song_file_info.file_name)
                try:
                    # Update all metadata but do not update the track num to avoid resorting playlist
                    force_update_file_name = generate_metadata(file_path, link, song_file_info.track_num, playlist["title"], config, regenerate_metadata, True)
                    force_update_file_path = os.path.join("playlist-download", playlist_name, force_update_file_name)
                    if file_path != force_update_file_path:
                        # Track name needs updating to proper format
                        print(f"{Fore.LIGHTGREEN_EX}Renomeando nome de arquivo incorreto de '{Path(file_path).stem}'para '{Path(force_update_file_path).stem}'")
                        os.rename(file_path, force_update_file_path)
                except Exception as e:
                    print(f"{Fore.LIGHTRED_EX}Não foi possível atualizar os metadados: {e}")
            else:
                print(f"{Fore.LIGHTRED_EX}Não foi possível atualizar os metadados de '{link}': esta música ainda não foi baixada, atualize a playlist primeiro")

            # Updating single song finished
            return

        if song_file_info is None:
            # Download audio if not downloaded
            print(f"{Fore.GREEN}Baixando '{link}'... ({track_num}/{len(playlist_entries) - skipped_videos})")
            
            if base_config["use_threading"]:
                download_futures.append(download_executor.submit(download_song_and_update, video_info, playlist, link, playlist_name, track_num, config))
            else:
                error_message, _ = download_song_and_update(video_info, playlist, link, playlist_name, track_num, config)
                if error_message is not None:
                    print(error_message)
                    skipped_videos += 1
        else:
            # Skip downloading audio if already downloaded
            print(f"{Fore.RED}Download ignorado '{link}' ({track_num}/{len(playlist_entries) - skipped_videos})")

            if base_config["use_threading"]:
                # Defer updating track num when using threading
                file_path = os.path.join("playlist-download", playlist_name, song_file_info.file_name)
            else:
                # Update track num and get file path
                file_path = update_file_order(os.path.join("playlist-download", playlist_name), song_file_info, track_num, config, False)

            # Generate metadata just in case it is missing
            if base_config["use_threading"]:
                update_futures.append(update_executor.submit(update_song, video_info, song_file_info, file_path, link, track_num, playlist["title"], config, regenerate_metadata, force_update))
            else:
                error_message = update_song(video_info, song_file_info, file_path, link, track_num, playlist["title"], config, regenerate_metadata, force_update)
                if error_message is not None:
                    print(error_message)

    # Update track nums after download and update when using threading
    if base_config["use_threading"]:
        results = []

        # Gather all results in order of submission
        for index, task in enumerate(download_futures):
            error_message, track_num = task.result()
            results.append((error_message, track_num))
            if error_message is not None:
                print(error_message)

        for index, task in enumerate(update_futures):
            error_message = task.result()
            if error_message is not None:
                print(error_message)

        # Explicitly shutdown executors
        download_executor.shutdown(wait=False)
        update_executor.shutdown(wait=False)

        # Get all new temporary song file infos for existing and newly downloaded songs and update
        skipped_track_nums = [track_num for (error_message, track_num) in results if error_message is not None]
        temp_song_file_infos = get_song_file_infos(os.path.join("playlist-download", playlist_name)) # May raise exception for duplicate songs
        for i, video_info in enumerate(playlist_entries):
            if video_info is None:
                # Dummy spacer entry to retain index order
                continue

            # Skip videos that failed to download
            original_track_num = i + 1
            track_num = original_track_num - skipped_videos
            if original_track_num in skipped_track_nums:
                skipped_videos += 1
                continue

            video_id = video_info["id"]
            temp_song_file_info = temp_song_file_infos.get(video_id)
            if temp_song_file_info is not None:
                # Update file path and track num
                config = get_override_config(video_id, base_config)
                file_path = update_file_order(os.path.join("playlist-download", playlist_name), temp_song_file_info, track_num, config, False)

    # Song not found for single song update
    if track_num_to_update is not None:
        print(f"{Fore.RED}Não foi possível atualizar os metadados da música #{track_num_to_update}: esta música não foi encontrada ou está indisponível. Atualize a playlist primeiro")
        return

    # Move songs that are missing (deleted/privated/etc.) to end of the list
    track_num = len(playlist_entries) - skipped_videos + 1
    for video_id in song_file_infos.keys():
        if video_id not in updated_video_ids:
            # Update file path and track num
            config = get_override_config(video_id, base_config)
            song_file_info = song_file_infos[video_id]
            file_path = update_file_order(os.path.join("playlist-download", playlist_name), song_file_info, track_num, config, True)
            track_num += 1

    print(f"{Fore.LIGHTGREEN_EX}Download concluído.")

def get_existing_playlists(directory: str, config_file_name: str):
    playlists_data = []
    playlists_name_dict = {}
    duplicate_playlists = {}
    for playlist_name in next(os.walk(os.path.join("playlist-download", directory)))[1]:
        config_file = os.path.join("playlist-download", directory, playlist_name, config_file_name)
        if os.path.exists(config_file):
            try:
                with open(config_file, "r") as f:
                    config = json.load(f)
            except json.decoder.JSONDecodeError as e:
                print(e)
                print(f"{Fore.RED}[ERROR] O arquivo de configuração '{config_file}' está em um formato inválido. Corrija ou remova o arquivo de configuração.")
                continue

            try:
                playlist_id = get_url_parameter(config["url"], "list")
            except:
                print(f"{Fore.RED}[ERROR] O URL da lista de reprodução no arquivo de configuração '{config_file}' está em um formato inválido. Corrija ou remova o arquivo de configuração.")
                continue

            if playlist_id in playlists_name_dict:
                # Check for duplicate playlists
                if playlist_id not in duplicate_playlists:
                    duplicate_playlists[playlist_id] = [playlists_name_dict[playlist_id]]

                duplicate_playlists[playlist_id].append(playlist_name)
                continue

            playlist_data = {
                "playlist_name": playlist_name,
                "config_file": config_file,
                "last_updated": time.strftime('%x %X', time.localtime(os.path.getmtime(config_file)))
            }
            playlists_data.append(playlist_data)
            playlists_name_dict[playlist_id] = playlist_name

    if duplicate_playlists:
        exception_strings = []
        for playlist_id, playlist_names in duplicate_playlists.items():
            exception_strings.append(Fore.RED + "\n".join([
                "As seguintes pastas de playlist estão vinculadas ao mesmo ID da playlist",
                f"{Fore.RED}ID da lista de reprodução duplicado: '{playlist_id}'",
                "\n".join(["- " + playlist_name for playlist_name in playlist_names])
            ]))

        raise FileExistsError(Fore.RED + "\n".join([
            "",
            "==============================================================================",
            "[ERROR] Pastas de listas de reprodução duplicadas encontradas neste diretório!",
            "==============================================================================",
            "\n\n".join(exception_strings),
            "==============================================================================",
            "Por favor, remova listas de reprodução duplicadas para resolver este conflito.",
            "==============================================================================",
            ""
        ]))

    return playlists_data

def get_bool_option_response(prompt, default: bool):
    if default:
        prompt_choice = "S/n"
    else:
        prompt_choice = "s/N"

    while True:
        response = input(f"{prompt} ({prompt_choice}): ").lower()
        if response == "s" or (default and response == ""):
            return True
        elif response == "n" or (not default and response == ""):
            return False
        else:
            print(Fore.RED + "Resposta invalida, por favor digite 's' ou 'n'.")

def get_index_option_response(prompt, count: int):
    if count <= 0:
        raise Exception(f"{Fore.LIGHTRED_EX}A contagem deve ser maior que 0")

    index = 0
    while True:
        selected_index = input(f"{prompt} (1 de {count}): ")
        try:
            index = int(selected_index) - 1
            if index >= 0 and index < count:
                break
        except:
            pass
        
        print(f"{Fore.RED}Resposta inválida, insira um número válido.")

    return index

def get_numeric_option_response(prompt):
    index = 0
    while True:
        selected_index = input(f"{prompt}: ")
        try:
            index = int(selected_index)
            if index > 0:
                break
        except:
            pass
        
        print(f"{Fore.RED}Resposta inválida. Insira um número válido maior que 0.")

    return index

if __name__ == "__main__":
    print(Fore.LIGHTGREEN_EX + "\n".join([
        "YouTube Music Download v" + version,
        "-----------------------------------------------------------",
        'Este programa faz o download e atualiza automaticamente uma cópia local',
        'de qualquer lista de reprodução do YouTube na forma de uma pasta de álbum de música',
        '- As músicas são armazenadas em pastas de álbuns nomeadas pelo título da playlist',
        '- Os álbuns existentes são atualizados com músicas novas ou ausentes',
        '- Músicas faltantes são enviadas para o final do álbum [alternar na configuração]',
        '- Os metadados da música são gerados automaticamente usando informações do vídeo',
        '- Os metadados incluem título/artistas/álbum/letras/número de faixas',
        '- As capas das músicas são criadas usando miniaturas de vídeo',
        '',
        '[NOTA] Este programa e o ffmpeg podem estar bloqueados por antivírus.',
        'Se você tiver algum problema, você pode tentar adicionar este programa',
        'e sua pasta ffmpeg com as exclusões do seu antivírus.',
        "-----------------------------------------------------------",
    ]))

    quit_enabled = True
    config_file_name = "playlist_config.json"

    OPTION_DOWNLOAD = f"{Fore.LIGHTGREEN_EX}Baixar playlist do Youtube"
    OPTION_SINGLE_SONG = "Baixar uma única música do Youtube"  # Adicione esta linha
    OPTION_UPDATE   = "Atualizar playlist salva anteriormente"
    OPTION_SONG     = "Atualizar uma única música na playlist"
    OPTION_MODIFY   = "Modificar lista de reprodução salva anteriormente"
    OPTION_GENERATE = "Gerar configuração de lista de reprodução padrão"
    OPTION_CHANGE   = "Alterar o diretório de trabalho atual"
    OPTION_EXIT     = "Sair"

    single_playlist = os.path.exists(config_file_name)
    if single_playlist:
        print(f"{Fore.RED}Pasta atual detectada como uma lista de reprodução. Executando no modo de lista de reprodução única.\nSe você não esperava por isso, remova '{config_file_name}' desta pasta.")

    while True:
        try:
            check_ffmpeg()

            config = {}
            playlists_data = {}
            quit_enabled = True
            selected_option = None
            existing_config = None
            update_existing = False
            modify_existing = False
            regenerate_metadata = False
            current_playlist_name = None

            options = [
                OPTION_DOWNLOAD,
                OPTION_SINGLE_SONG,  # Adicione esta linha
                OPTION_UPDATE,
                OPTION_SONG,
                OPTION_MODIFY,
                OPTION_GENERATE,
                OPTION_CHANGE,
                OPTION_EXIT
            ]

            if single_playlist:
                # Single playlist in current directory
                while True:
                    try:
                        with open(config_file_name, "r") as f:
                            config = json.load(f)
                        modify_existing = True
                        existing_config = config
                        current_playlist_name = os.path.basename(os.getcwd())
                    except (KeyboardInterrupt, EOFError) as e:
                        raise e
                    except json.decoder.JSONDecodeError as e:
                        print(f"{Fore.RED}\n{e}\n[ERROR] O arquivo de configuração '{config_file_name}' está em um formato inválido. Corrija ou remova o arquivo de configuração.")
                        quit_enabled = True
                        input(Fore.GREEN + "Pressione 'Enter' para continuar após resolver este conflito ou feche esta janela para terminar.")
                        continue
                    except:
                        print(f"{Fore.RED}\n[ERROR] O arquivo de configuração '{config_file_name}' não foi encontrado. Certifique-se de que o arquivo de configuração esteja presente.")
                        quit_enabled = True
                        input(Fore.GREEN + "Pressione 'Enter' para continuar após resolver este conflito ou feche esta janela para terminar.")
                        
                    break
            else:
                # Multiple playlists in sub-directories
                while True:
                    try:
                        playlists_data = get_existing_playlists(".", config_file_name)
                    except FileExistsError as e:
                        print(e)
                        quit_enabled = True
                        input(Fore.GREEN + "Pressione 'Enter' para continuar após resolver este conflito ou feche esta janela para terminar.")
                        continue
                    except (KeyboardInterrupt, EOFError) as e:
                        raise e
                    except Exception as e:
                        print(e)
                        print(f"{Fore.RED}Falha ao obter uma lista de playlists existentes")
                    break
                if len(playlists_data) > 0:
                    options.insert(1, OPTION_UPDATE)
                    options.insert(2, OPTION_SONG)
                    options.insert(3, OPTION_MODIFY)

                options_formatted = []
                for i, option in enumerate(options):
                    options_formatted.append(f"{i + 1}. {option}")
                print(f"\n" + "\n".join(options_formatted) + "\n")

                selected_option = options[get_index_option_response("Selecione uma opção", len(options))]

            quit_enabled = False
            if selected_option == OPTION_DOWNLOAD:
                output_folder = "playlist-download"
                # Download new playlist
                config = setup_config(config)
                config["url"] = input(f"{Fore.GREEN}Por favor, insira o URL da playlist que você deseja baixar: ")

                # Check if playlist is already downloaded
                already_downloaded = False
                for playlist_data in playlists_data:
                    try:
                        with open(playlist_data["config_file"], "r") as f:
                            existing_config = json.load(f)

                        if get_url_parameter(existing_config["url"], "list") == get_url_parameter(config["url"], "list"):
                            # Playlist already downloaded
                            already_downloaded = True
                            print(Fore.BLUE + "\n" + f"> {playlist_data['playlist_name']} (Ultima Atualização: {playlist_data['last_updated']})" + "\n")
                            update_existing = get_bool_option_response(Fore.BLUE + "Esta lista de reprodução já foi baixada. Atualizar lista de reprodução?", default=True)
                            if not update_existing:
                                print(Fore.RED + "Playlist existente não atualizada")
                                quit_enabled = True
                                input(Fore.GREEN + "Pressione 'Enter' para retornar ao menu principal ou feche esta janela para finalizar.")
                            else:
                                current_playlist_name = playlist_data["playlist_name"]
                            break
                    except KeyboardInterrupt as e:
                        raise e
                    except:
                        continue

                if not already_downloaded and not update_existing:
                    config["reverse_playlist"] = get_bool_option_response(Fore.BLUE + "Playlist reversa?", default=False)
                    config["use_title"] = get_bool_option_response(Fore.BLUE + "Usar título em vez do nome da faixa?", default=True)
                    config["use_uploader"] = get_bool_option_response(Fore.BLUE + "Usar uploader em vez de artista?", default=True)
                    config["use_playlist_name"] = get_bool_option_response(Fore.BLUE + "Usar o nome da playlist para o álbum?", default=True)

                    generate_playlist(config, config_file_name, False, False, regenerate_metadata, False, current_playlist_name, None)
                    quit_enabled = True
                    input(Fore.LIGHTGREEN_EX + "O download foi concluído. Pressione 'Enter' para retornar ao menu principal ou feche esta janela para finalizar.")

            if selected_option == OPTION_SINGLE_SONG:
                url = input(f"{Fore.GREEN}Por favor, insira o URL da música que você deseja baixar: ")
                output_folder = "music-download"
                Path(output_folder).mkdir(parents=True, exist_ok=True)
                download_single_song(url, output_folder)
                quit_enabled = True
                input(Fore.LIGHTGREEN_EX + "O download foi concluído. Pressione 'Enter' para retornar ao menu principal ou feche esta janela para finalizar.")

            if selected_option == OPTION_UPDATE or selected_option == OPTION_SONG or update_existing:
                # Update existing playlist
                config = None
                if update_existing:
                    config = existing_config
                else:
                    playlists_list = []
                    for i, playlist_data in enumerate(playlists_data):
                        playlists_list.append(f"{Fore.LIGHTBLUE_EX}{i + 1}. {playlist_data['playlist_name']} (Ultima Atualização: {playlist_data['last_updated']})")
                    print("\n" + "\n".join(playlists_list) + "\n")

                    update_index = get_index_option_response(Fore.BLUE + "Digite um número de lista de reprodução para atualizar", len(playlists_data))
                    playlist_data = playlists_data[update_index]

                    current_playlist_name = playlist_data["playlist_name"]
                    with open(playlist_data["config_file"], "r") as f:
                        config = json.load(f)

                # In case settings were somehow missing
                config = setup_config(config)

                print("\n" + "\n".join([
                    f"{Fore.LIGHTBLUE_EX}Atualizando playliist: {current_playlist_name}",
                    f"URL: {config['url']}",
                ]) + "\n")

                track_num_to_update = None
                if selected_option == OPTION_SONG:
                    track_num_to_update = get_numeric_option_response(Fore.BLUE + "Insira um número de faixa de música para atualizar")

                quit_enabled = False
                generate_playlist(config, config_file_name, True, False, False, single_playlist, current_playlist_name, track_num_to_update)
                quit_enabled = True
                input(Fore.LIGHTGREEN_EX + "Atualização concluída. Pressione 'Enter' para retornar ao menu principal ou feche esta janela para finalizar.")

            if selected_option == OPTION_MODIFY or modify_existing:
                # Modify existing playlist
                config = None
                if modify_existing:
                    config = existing_config
                else:
                    playlists_list = []
                    for i, playlist_data in enumerate(playlists_data):
                        playlists_list.append(f"{Fore.LIGHTBLUE_EX}{i + 1}. {playlist_data['playlist_name']} (Ultima Atualização: {playlist_data['last_updated']})")
                    print("\n" + "\n".join(playlists_list) + "\n")

                    update_index = get_index_option_response(Fore.BLUE + "Digite um número de lista de reprodução para atualizar", len(playlists_data))
                    playlist_data = playlists_data[update_index]

                    current_playlist_name = playlist_data["playlist_name"]
                    with open(playlist_data["config_file"], "r") as f:
                        config = json.load(f)

                # In case settings were somehow missing
                config = setup_config(config)

                print(Fore.LIGHTGREEN_EX + "\n" + "\n".join([
                    f"Atualizando Playlisy: {current_playlist_name}",
                    f"URL: {config['url']}",
                    "",
                    f"Configurações da Playlist",
                    f"- Playlist reversa: {config['reverse_playlist']}",
                    f"- Use o título em vez do nome da faixa: {config['use_title']}",
                    f"- Use o uploader em vez do artista: {config['use_uploader']}",
                    f"- Usar o nome da playlist para o álbum: {config['use_playlist_name']}",
                ]) + "\n")

                if single_playlist:
                    quit_enabled = True

                modify_settings = get_bool_option_response(Fore.LIGHTBLUE_EX + "Usar o nome da playlist para o álbum?", default=False)
                quit_enabled = False

                update_single_song = False
                track_num_to_update = None
                if modify_settings:
                    last_use_title = config["use_title"]
                    last_use_uploader = config["use_uploader"]
                    last_use_playlist_name = config["use_playlist_name"]

                    config["reverse_playlist"] = get_bool_option_response(Fore.BLUE + "Playlist reversa?", default=False)
                    config["use_title"] = get_bool_option_response(Fore.BLUE + "Usar título em vez do nome da faixa?", default=True)
                    config["use_uploader"] = get_bool_option_response(Fore.BLUE + "Usar uploader em vez de artista?", default=True)
                    config["use_playlist_name"] = get_bool_option_response(Fore.BLUE + "Usar o nome da playlist para o álbum?: ", default=True)

                    # Metadata needs to be regenerated if the settings have been changed
                    if config["use_title"] != last_use_title or config["use_uploader"] != last_use_uploader or config["use_playlist_name"] != last_use_playlist_name:
                        regenerate_metadata = True
                elif single_playlist:
                    update_single_song = get_bool_option_response(Fore.BLUE + "Atualizar uma única música?", default=False)
                    if update_single_song:
                        track_num_to_update = get_numeric_option_response(Fore.BLUE + "Insira um número de faixa de música para atualizar")

                force_update = False
                if not update_single_song:
                    force_update = get_bool_option_response(Fore.BLUE + "Forçar a atualização de todos os nomes e metadados?", default=False)

                generate_playlist(config, config_file_name, True, force_update, regenerate_metadata, single_playlist, current_playlist_name, track_num_to_update)
                quit_enabled = True
                input(Fore.LIGHTGREEN_EX + "Atualização concluída. Pressione 'Enter' para retornar ao menu principal ou feche esta janela para finalizar.")

            if selected_option == OPTION_GENERATE:
                # Generate default playlist config
                config["url"] = input(Fore.BLUE + "Por favor, insira o URL da lista de reprodução para gerar a configuração para: ")

                # Check if playlist is already downloaded
                already_downloaded = False
                for playlist_data in playlists_data:
                    try:
                        with open(playlist_data["config_file"], "r") as f:
                            existing_config = json.load(f)

                        if get_url_parameter(existing_config["url"], "list") == get_url_parameter(config["url"], "list"):
                            print(f"{Fore.RED}A playlist '{playlist_data['playlist_name']}' já foi baixado.")
                            quit_enabled = True
                            input(f"{Fore.LIGHTGREEN_EX}Pressione 'Enter' para retornar ao menu principal ou feche esta janela para finalizar.")
                            already_downloaded = True
                            break
                    except KeyboardInterrupt as e:
                        raise e
                    except:
                        continue

                if not already_downloaded:
                    generate_default_config(config, config_file_name)
                    quit_enabled = True
                    input(Fore.LIGHTGREEN_EX + "Concluída a geração da configuração padrão. Pressione 'Enter' para retornar ao menu principal ou feche esta janela para finalizar.")

            if selected_option == OPTION_CHANGE:
                # Change current working directory
                target_path = input(Fore.BLUE + "Insira o caminho da pasta de destino onde sera salva as playlists: ")
                os.chdir(target_path)

            if selected_option == OPTION_EXIT:
                # Exit
                quit_enabled = True
                raise KeyboardInterrupt
        except (KeyboardInterrupt, EOFError):
            if quit_enabled:
                print(Fore.LIGHTMAGENTA_EX + "\nSaindo...")
                break

            print(Fore.RED + "\nCancelando...\nPara sair, selecione Sair ou pressione Ctrl+C novamente)")
            continue
        except Exception as e:
            print(e)
            print(Fore.RED + "Erro encontrado durante a geração. Por favor, tente novamente.")
            continue

    # Suppress additional messages
    sys.exit()