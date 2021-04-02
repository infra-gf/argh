import functools
import logging
import traceback
import sys
import os
import urllib.request
import calendar
import random

from flask import Flask, Blueprint, current_app, request, redirect, abort, Response
import youtube_dl
import utwee
import arrow
import utwint.get
from youtube_dl.version import __version__ as youtube_dl_version


from flask import Flask, render_template

service = os.environ.get("K_SERVICE", "Unknown service")
revision = os.environ.get("K_REVISION", "Unknown revision")


if not hasattr(sys.stderr, "isatty"):
    # In GAE it's not defined and we must monkeypatch
    sys.stderr.isatty = lambda: False

import json
from flask import make_response


def jsonify(arg, status=200, indent=4, sort_keys=True, **kwargs):
    response = make_response(json.dumps(dict(arg), indent=indent, sort_keys=sort_keys))
    response.headers["Content-Type"] = "text/plain; charset=utf-8"
    response.headers["mimetype"] = "text/plain"
    response.status_code = status
    return response


class SimpleYDL(youtube_dl.YoutubeDL):
    def __init__(self, *args, **kargs):
        super(SimpleYDL, self).__init__(*args, **kargs)
        self.add_default_info_extractors()


def get_videos(url, extra_params):
    """
    Get a list with a dict for every video founded
    """
    ydl_params = {
        "format": "best",
        "cachedir": False,
        "logger": current_app.logger.getChild("youtube-dl"),
    }
    ydl_params.update(extra_params)
    ydl = SimpleYDL(ydl_params)
    res = ydl.extract_info(url, download=False)
    return res


def flatten_result(result):
    r_type = result.get("_type", "video")
    if r_type == "video":
        videos = [result]
    elif r_type == "playlist":
        videos = []
        for entry in result["entries"]:
            videos.extend(flatten_result(entry))
    elif r_type == "compat_list":
        videos = []
        for r in result["entries"]:
            videos.extend(flatten_result(r))
    return videos


api = Blueprint("api", __name__)


def route_api(subpath, *args, **kargs):
    return api.route("/api/" + subpath, *args, **kargs)


def set_access_control(f):
    @functools.wraps(f)
    def wrapper(*args, **kargs):
        response = f(*args, **kargs)
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    return wrapper


@api.errorhandler(youtube_dl.utils.DownloadError)
@api.errorhandler(youtube_dl.utils.ExtractorError)
def handle_youtube_dl_error(error):
    logging.error(traceback.format_exc())
    result = jsonify({"error": str(error)})
    result.status_code = 500
    return result


class WrongParameterTypeError(ValueError):
    def __init__(self, value, type, parameter):
        message = '"{}" expects a {}, got "{}"'.format(parameter, type, value)
        super(WrongParameterTypeError, self).__init__(message)


@api.errorhandler(WrongParameterTypeError)
def handle_wrong_parameter(error):
    logging.error(traceback.format_exc())
    result = jsonify({"error": str(error)})
    result.status_code = 400
    return result


@api.before_request
def block_on_user_agent():
    user_agent = request.user_agent.string
    forbidden_uas = current_app.config.get("FORBIDDEN_USER_AGENTS", [])
    if user_agent in forbidden_uas:
        abort(429)


def query_bool(value, name, default=None):
    if value is None:
        return default
    value = value.lower()
    if value == "true":
        return True
    elif value == "false":
        return False
    else:
        raise WrongParameterTypeError(value, "bool", name)


ALLOWED_EXTRA_PARAMS = {
    "format": str,
    "playliststart": int,
    "playlistend": int,
    "playlist_items": str,
    "playlistreverse": bool,
    "matchtitle": str,
    "rejecttitle": str,
    "writesubtitles": bool,
    "writeautomaticsub": bool,
    "allsubtitles": bool,
    "subtitlesformat": str,
    "subtitleslangs": list,
}


def get_result():
    url = request.args["url"]
    extra_params = {}
    for k, v in request.args.items():
        if k in ALLOWED_EXTRA_PARAMS:
            convertf = ALLOWED_EXTRA_PARAMS[k]
            if convertf == bool:
                convertf = lambda x: query_bool(x, k)
            elif convertf == list:
                convertf = lambda x: x.split(",")
            extra_params[k] = convertf(v)
    return get_videos(url, extra_params)


@route_api("info")
@set_access_control
def info():
    url = request.args["url"]
    result = get_result()
    key = "info"
    if query_bool(request.args.get("flatten"), "flatten", False):
        result = flatten_result(result)
        key = "videos"
    return jsonify(result)


@route_api("play")
def play():
    result = flatten_result(get_result())
    return redirect(result[0]["url"])


@route_api("extractors")
@set_access_control
def list_extractors():
    ie_list = [
        {
            "name": ie.IE_NAME,
            "working": ie.working(),
        }
        for ie in youtube_dl.gen_extractors()
    ]
    return jsonify({"extractors": ie_list})


@route_api("version")
@set_access_control
def version():
    result = {
        "youtube-dl": youtube_dl_version,
        "argh": 0.1,
        "twint": utwee.twint_version,
    }
    return jsonify(result)


@route_api("current_ip")
@set_access_control
def current_ip():
    res = urllib.request.urlopen("https://ipinfo.io").read().decode()
    return jsonify(json.loads(res))


@route_api("headers")
@set_access_control
def headers():
    return jsonify(dict(request.headers))


@route_api("tweep")
@set_access_control
def tweep():
    username = request.args["username"]
    limit = int(request.args["limit"] or 100)
    return Response(utwee.generate_response(username, limit), mimetype="text/plain")


def get_tweet_metadata_secret_api_bad_tech(status_id):
    # this is interesting, but a kinda terrible way of doing it...
    syndication_query = (
        "https://root.tweeter.workers.dev/tweet?host=cdn.syndication.twimg.com&id="
        + str(status_id)
    )
    synd_resp = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                syndication_query,
                headers={"User-Agent": random.choice(utwint.get.user_agent_list)},
            )
        ).read()
    )
    return synd_resp


def get_embed_by_id(status_id):
    oembed_query = (
        "http://root.tweeter.workers.dev/oembed?host=publish.twitter.com&dnt=true&omit_script=true&url=https://mobile.twitter.com/i/status/"
        + str(status_id)
    )
    embed_resp = json.loads(urllib.request.urlopen(oembed_query).read())
    return embed_resp


@route_api("tw_metadata")
@set_access_control
def tw_metadata():
    status_id = request.args.get("id")
    if not status_id:
        return Response("Try again with ?id=<status number>")
    # if a URL was passed, grab the last fragment and pretend it's a status ID
    if "/" in status_id:
        status_id = status_id.strip("/").split("/")[-1]
    return jsonify(get_tweet_metadata_secret_api_bad_tech(status_id))


@route_api("tw_replies")
@set_access_control
def tw_replies():
    url = request.args.get("url")
    # allow &all=true to disable the extra filter step
    get_all = request.args.get("all")
    if not (url and url.count("/") in (3, 5)):
        return Response("Try again with ?url=https://twitter.com/account/status/...")
    tweet_id = url.rstrip("/").split("/")[-1]
    username = url.rstrip("/").split("/")[-3]
    # very lame way of getting the date of the tweet with a single (albeit synchronous) request
    oembed_query = (
        "https://publish.twitter.com/oembed?dnt=true&omit_script=true&url=" + url
    )
    embed_resp = json.loads(urllib.request.urlopen(oembed_query).read())
    html = embed_resp.get("html") or ""
    if not html:
        return Response(f"Tweet {url} could not be found for embed.")
    date = html.split('ref_src=twsrc%5Etfw">')[-1].split("</a>")[0]
    (month, day, year) = date.split(" ")
    month_index = list(calendar.month_name).index(month)
    day = day.strip(",")
    day, year = int(day), int(year)
    # okay, now we have three integers - pass tem into an Arrow object, and use arrow's calculator to do the timeshifts.
    publish_date = arrow.Arrow(month=month_index, day=day, year=year)
    Since = publish_date.shift(days=-1).format("YYYY-MM-DD")
    Until = publish_date.shift(days=7).format("YYYY-MM-DD")
    # uh just roll with it, okay
    responses = [
        response
        for response in reversed(
            [
                {k: v for k, v in json.loads(r).items() if v}
                for r in utwee.generate_response(username, limit=250)
            ]
        )
        if get_all
        or (
            get_tweet_metadata_secret_api_bad_tech(response.get("id")).get(
                "in_reply_to_status_id_str"
            )
            == tweet_id
        )
    ]
    return Response(json.dumps(responses, indent=2), mimetype="text/plain")


app = Flask("__main__")
app.register_blueprint(api)

if __name__ == "__main__":
    server_port = os.environ.get("PORT", "8080")
    app.run(debug=False, port=server_port, host="0.0.0.0")
