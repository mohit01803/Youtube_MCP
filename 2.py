# server.py — Enhanced MCP server for YouTube data (HTTP Streamable) with detailed logging
from typing import Any, Dict, List, Optional

# --- FastMCP import shim (supports both package layouts) ---
try:
    # Preferred modern package
    from fastmcp import FastMCP
except ImportError:
    # Older installs expose it here
    from mcp.server.fastmcp import FastMCP

import httpx
import os
import re
import json
import logging
import sys
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
from datetime import datetime

# Configure logging
# logging.basicConfig(
#    level=logging.INFO,
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#     handlers=[
#         logging.FileHandler('mcp_server.log'),
#         logging.StreamHandler(sys.stderr)
#     ]
# )
logger = logging.getLogger(__name__)

mcp = FastMCP("youtubemcp")

load_dotenv()
YT_API_KEY = None
YT_BASE = "https://www.googleapis.com/youtube/v3"

logger.info("=" * 60)
logger.info("Enhanced YouTube MCP Server Starting...")
logger.info("=" * 60)

def _get_yt_api_key() -> str:
    """
    Get YouTube API key from command line arguments or environment variable.
    Caches the key in memory after first read.

    Sources (in order):
      1) --yt-key CLI flag
      2) YOUTUBE_API_KEY environment variable

    Raises:
        Exception: If no key is provided via CLI or environment.
    """
    global YT_API_KEY
    if YT_API_KEY is None:
        # 1) CLI: --yt-key <value>
        if "--yt-key" in sys.argv:
            key_index = sys.argv.index("--yt-key") + 1
            if key_index < len(sys.argv):
                YT_API_KEY = sys.argv[key_index]
                logger.info("Using YouTube API key from command line arguments")
                return YT_API_KEY
            else:
                raise Exception("--yt-key argument provided but no key value followed it")

        # 2) Environment variable
        env_key = os.getenv("YOUTUBE_API_KEY")
        if env_key:
            YT_API_KEY = env_key
            logger.info("Using YouTube API key from YOUTUBE_API_KEY environment variable")
            return YT_API_KEY

        # Fail if neither provided
        raise Exception(
            "YouTube API key is required. Provide via '--yt-key <KEY>' or set YOUTUBE_API_KEY env var."
        )

    return YT_API_KEY

def _extract_video_id(video_url: str) -> str:
    """Extract video ID from various YouTube URL formats"""
    logger.info(f"Extracting video ID from URL: {video_url}")
    try:
        u = urlparse(video_url)
        if u.netloc.endswith("youtu.be"):
            return u.path.strip("/")
        if "youtube.com" in u.netloc:
            qs = parse_qs(u.query or "")
            if "v" in qs:
                return qs["v"][0]
            m = re.search(r"/embed/([A-Za-z0-9_-]{6,})", u.path or "")
            if m:
                return m.group(1)
        m = re.search(r"([A-Za-z0-9_-]{11})", video_url)
        return m.group(1) if m else ""
    except Exception as e:
        logger.error(f"Error extracting video ID: {e}")
        return ""

def _extract_channel_id(channel_input: str) -> tuple[str, str]:
    """Extract channel ID or username from URL or handle"""
    logger.info(f"Extracting channel info from: {channel_input}")
    try:
        # If it's a URL
        if "youtube.com" in channel_input:
            u = urlparse(channel_input)
            # Handle /channel/CHANNEL_ID format
            if "/channel/" in u.path:
                channel_id = u.path.split("/channel/")[1].split("/")[0]
                return ("id", channel_id)
            # Handle /@username format
            if "/@" in u.path:
                username = u.path.split("/@")[1].split("/")[0]
                return ("username", username)
            # Handle /c/ or /user/ format
            if "/c/" in u.path or "/user/" in u.path:
                username = u.path.split("/")[-1]
                return ("username", username)
        # If it starts with @, it's a handle
        elif channel_input.startswith("@"):
            return ("username", channel_input[1:])
        # Otherwise treat as channel ID
        else:
            return ("id", channel_input)
    except Exception as e:
        logger.error(f"Error extracting channel info: {e}")
        return ("id", channel_input)

async def _yt_get(client: httpx.AsyncClient, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Make YouTube API request"""
    params = dict(params)
    api_key = _get_yt_api_key()
    params["key"] = api_key
    logger.info(f"Making YouTube API request to: {path}")
    r = await client.get(f"{YT_BASE}/{path}", params=params, timeout=30.0)
    r.raise_for_status()
    logger.info(f"API request successful - Status: {r.status_code}")
    return r.json()

def _pack_comment(item: Dict[str, Any], parent_id: Optional[str] = None) -> Dict[str, Any]:
    """Pack comment data"""
    s = item.get("snippet", {})
    return {
        "id": item.get("id"),
        "parentId": parent_id,
        "author": s.get("authorDisplayName"),
        "publishedAt": s.get("publishedAt"),
        "likeCount": s.get("likeCount", 0),
        "text": s.get("textOriginal") or s.get("textDisplay") or "",
    }

def _pack_video(item: Dict[str, Any]) -> Dict[str, Any]:
    """Pack video data"""
    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})
    content_details = item.get("contentDetails", {})
    
    return {
        "videoId": item.get("id"),
        "title": snippet.get("title"),
        "channelTitle": snippet.get("channelTitle"),
        "channelId": snippet.get("channelId"),
        "publishedAt": snippet.get("publishedAt"),
        "description": snippet.get("description", "")[:200],  # Truncate
        "thumbnails": snippet.get("thumbnails", {}),
        "viewCount": int(statistics.get("viewCount", 0)),
        "likeCount": int(statistics.get("likeCount", 0)),
        "commentCount": int(statistics.get("commentCount", 0)),
        "duration": content_details.get("duration"),
        "tags": snippet.get("tags", [])
    }

def _pack_artist(item: Dict[str, Any]) -> Dict[str, Any]:
    """Pack artist data"""
    snippet = item.get("snippet", {})
    statistics = item.get("statistics", {})
    
    return {
        "channelId": item.get("id"),
        "title": snippet.get("title"),
        "description": snippet.get("description", "")[:200],
        "thumbnails": snippet.get("thumbnails", {}),
        "subscriberCount": int(statistics.get("subscriberCount", 0)),
        "videoCount": int(statistics.get("videoCount", 0)),
        "viewCount": int(statistics.get("viewCount", 0)),
        "country": snippet.get("country"),
        "publishedAt": snippet.get("publishedAt")
    }

@mcp.tool()
async def fetch_comments(videoUrl: str, order: str = "relevance", max: int = 300) -> str:
    """
    Fetch public comments for a YouTube video.
    Args:
      videoUrl: Full YouTube video URL.
      order: "relevance" (default) or "time".
      max: Max total comments to return (100–1000 recommended).
    """
    logger.info(f"fetch_comments called - URL: {videoUrl}, order: {order}, max: {max}")
    
    try:
        _get_yt_api_key()
    except Exception as e:
        return f"ERROR: {str(e)}"
    
    video_id = _extract_video_id(videoUrl)
    if not video_id:
        return "ERROR: Cannot parse video ID from URL."
    
    order = order if order in ("relevance", "time") else "relevance"
    items: List[Dict[str, Any]] = []
    page_token = None
    
    try:
        async with httpx.AsyncClient() as client:
            while len(items) < max:
                params = {
                    "part": "snippet,replies",
                    "videoId": video_id,
                    "maxResults": 100,
                    "order": order,
                    "textFormat": "plainText",
                }
                if page_token:
                    params["pageToken"] = page_token
                    
                data = await _yt_get(client, "commentThreads", params)

                for th in data.get("items", []):
                    top = th.get("snippet", {}).get("topLevelComment", {})
                    if top:
                        items.append(_pack_comment(top))
                    for rep in th.get("replies", {}).get("comments", []) or []:
                        items.append(_pack_comment(rep, parent_id=top.get("id") if top else None))
                    if len(items) >= max:
                        break

                page_token = data.get("nextPageToken")
                if not page_token:
                    break

        logger.info(f"Successfully fetched {len(items)} comments")
        return json.dumps({
            "video_id": video_id,
            "order": order,
            "total_returned": len(items),
            "items": items
        }, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error: {e}")
        return f"ERROR: {type(e).__name__}: {e}"

@mcp.tool()
async def get_channel_videos(channelInput: str, order: str = "viewCount", max: int = 500) -> str:
    """
    Get videos from a YouTube channel.
    Args:
      channelInput: Channel URL, channel ID, or @username
      order: "viewCount" (most viewed), "date" (newest), "rating" (highest rated)
      max: Max videos to return (1-50)
    """
    logger.info(f"get_channel_videos called - Channel: {channelInput}, order: {order}, max: {max}")
    
    try:
        _get_yt_api_key()
    except Exception as e:
        return f"ERROR: {str(e)}"
    
    try:
        async with httpx.AsyncClient() as client:
            # First, resolve channel ID
            id_type, identifier = _extract_channel_id(channelInput)
            
            if id_type == "username":
                # Search for channel by username
                data = await _yt_get(client, "channels", {
                    "part": "id,snippet",
                    "forHandle": identifier if not identifier.startswith("@") else identifier,
                })
                if not data.get("items"):
                    return f"ERROR: Channel not found for username: {identifier}"
                channel_id = data["items"][0]["id"]
            else:
                channel_id = identifier
            
            logger.info(f"Resolved channel ID: {channel_id}")
            
            # Get channel's uploads playlist
            channel_data = await _yt_get(client, "channels", {
                "part": "contentDetails,snippet,statistics",
                "id": channel_id
            })
            
            if not channel_data.get("items"):
                return "ERROR: Channel not found"
            
            channel_info = channel_data["items"][0]
            uploads_playlist_id = channel_info["contentDetails"]["relatedPlaylists"]["uploads"]
            
            logger.info(f"Getting videos from uploads playlist: {uploads_playlist_id}")
            
            # Get videos from uploads playlist
            videos = []
            page_token = None
            
            while len(videos) < max:
                params = {
                    "part": "snippet,contentDetails",
                    "playlistId": uploads_playlist_id,
                    "maxResults": min(50, max - len(videos))
                }
                if page_token:
                    params["pageToken"] = page_token
                
                playlist_data = await _yt_get(client, "playlistItems", params)
                
                video_ids = [item["contentDetails"]["videoId"] for item in playlist_data.get("items", [])]
                
                if video_ids:
                    # Get detailed video stats
                    videos_data = await _yt_get(client, "videos", {
                        "part": "snippet,statistics,contentDetails",
                        "id": ",".join(video_ids)
                    })
                    
                    for video in videos_data.get("items", []):
                        videos.append(_pack_video(video))
                
                page_token = playlist_data.get("nextPageToken")
                if not page_token or len(videos) >= max:
                    break
            
            # Sort videos based on order parameter
            if order == "viewCount":
                videos.sort(key=lambda x: x["viewCount"], reverse=True)
            elif order == "date":
                videos.sort(key=lambda x: x["publishedAt"], reverse=True)
            elif order == "rating":
                videos.sort(key=lambda x: x["likeCount"], reverse=True)
            
            logger.info(f"Successfully fetched {len(videos)} videos from channel")
            
            return json.dumps({
                "channelId": channel_id,
                "channelTitle": channel_info["snippet"]["title"],
                "channelDescription": channel_info["snippet"]["description"][:200],
                "subscriberCount": channel_info["statistics"].get("subscriberCount", "Hidden"),
                "videoCount": channel_info["statistics"].get("videoCount", 0),
                "order": order,
                "total_returned": len(videos),
                "videos": videos[:max]
            }, ensure_ascii=False)
            
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return f"ERROR: {type(e).__name__}: {e}"

@mcp.tool()
async def search_videos(query: str, order: str = "viewCount", max: int = 200) -> str:
    """
    Search for YouTube videos.
    Args:
      query: Search query (e.g., "tseries most viewed song")
      order: "viewCount" (most viewed), "date" (newest), "rating" (highest rated), "relevance"
      max: Max results to return (1-50)
    """
    logger.info(f"search_videos called - Query: {query}, order: {order}, max: {max}")
    
    try:
        _get_yt_api_key()
    except Exception as e:
        return f"ERROR: {str(e)}"
    
    try:
        async with httpx.AsyncClient() as client:
            # Search for videos
            search_data = await _yt_get(client, "search", {
                "part": "id,snippet",
                "q": query,
                "type": "video",
                "order": order,
                "maxResults": max
            })
            
            video_ids = [item["id"]["videoId"] for item in search_data.get("items", [])]
            
            if not video_ids:
                return json.dumps({"error": "No videos found", "total_returned": 0, "videos": []})
            
            # Get detailed video stats
            videos_data = await _yt_get(client, "videos", {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(video_ids)
            })
            
            videos = [_pack_video(video) for video in videos_data.get("items", [])]
            
            logger.info(f"Successfully found {len(videos)} videos")
            
            return json.dumps({
                "query": query,
                "order": order,
                "total_returned": len(videos),
                "videos": videos
            }, ensure_ascii=False)
            
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return f"ERROR: {type(e).__name__}: {e}"

@mcp.tool()
async def get_trending_videos(region: str = "IN", category: str = "0", max: int = 200) -> str:
    """
    Get trending videos on YouTube.
    Args:
      region: Country code (IN=India, US=United States, GB=United Kingdom, etc.)
      category: Category ID ("0"=All, "10"=Music, "17"=Sports, "20"=Gaming, "24"=Entertainment)
      max: Max results to return (1-50)
    """
    logger.info(f"get_trending_videos called - Region: {region}, category: {category}, max: {max}")
    
    try:
        _get_yt_api_key()
    except Exception as e:
        return f"ERROR: {str(e)}"
    
    try:
        async with httpx.AsyncClient() as client:
            params = {
                "part": "snippet,statistics,contentDetails",
                "chart": "mostPopular",
                "regionCode": region.upper(),
                "maxResults": max
            }
            
            if category != "0":
                params["videoCategoryId"] = category
            
            data = await _yt_get(client, "videos", params)
            
            videos = [_pack_video(video) for video in data.get("items", [])]
            
            logger.info(f"Successfully fetched {len(videos)} trending videos")
            
            return json.dumps({
                "region": region,
                "category": category,
                "total_returned": len(videos),
                "videos": videos
            }, ensure_ascii=False)
            
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return f"ERROR: {type(e).__name__}: {e}"

@mcp.tool()
async def get_video_details(videoUrl: str) -> str:
    """
    Get detailed information about a specific video.
    Args:
      videoUrl: Full YouTube video URL or video ID
    """
    logger.info(f"get_video_details called - URL: {videoUrl}")
    
    try:
        _get_yt_api_key()
    except Exception as e:
        return f"ERROR: {str(e)}"
    
    video_id = _extract_video_id(videoUrl)
    if not video_id:
        return "ERROR: Cannot parse video ID from URL."
    
    try:
        async with httpx.AsyncClient() as client:
            data = await _yt_get(client, "videos", {
                "part": "snippet,statistics,contentDetails,status",
                "id": video_id
            })
            
            if not data.get("items"):
                return "ERROR: Video not found"
            
            video = _pack_video(data["items"][0])
            
            logger.info(f"Successfully fetched video details for: {video['title']}")
            
            return json.dumps(video, ensure_ascii=False)
            
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return f"ERROR: {type(e).__name__}: {e}"

@mcp.tool()
async def get_trending_playlists(region: str = "IN", max: int = 200) -> str:
    """
    Get popular and trending playlists on YouTube.
    Args:
      region: Country code (IN=India, US=United States, GB=United Kingdom, etc.)
      max: Max results to return (1-50)
    """
    logger.info(f"get_trending_playlists called - Region: {region}, max: {max}")
    
    try:
        _get_yt_api_key()
    except Exception as e:
        return f"ERROR: {str(e)}"
    
    try:
        async with httpx.AsyncClient() as client:
            # Search for trending playlists
            search_data = await _yt_get(client, "search", {
                "part": "snippet",
                "type": "playlist",
                "maxResults": max,
                "regionCode": region.upper(),
                "order": "viewCount",  # Sort by most viewed
            })
            
            playlists = []
            playlist_ids = [item["id"]["playlistId"] for item in search_data.get("items", [])]
            
            if playlist_ids:
                # Get detailed playlist information
                playlist_data = await _yt_get(client, "playlists", {
                    "part": "snippet,contentDetails",
                    "id": ",".join(playlist_ids)
                })
                
                for playlist in playlist_data.get("items", []):
                    playlists.append({
                        "playlistId": playlist["id"],
                        "title": playlist["snippet"]["title"],
                        "description": playlist["snippet"]["description"][:200],
                        "channelTitle": playlist["snippet"]["channelTitle"],
                        "channelId": playlist["snippet"]["channelId"],
                        "thumbnails": playlist["snippet"]["thumbnails"],
                        "videoCount": playlist["contentDetails"]["itemCount"],
                        "publishedAt": playlist["snippet"]["publishedAt"]
                    })
            
            logger.info(f"Successfully fetched {len(playlists)} trending playlists")
            
            return json.dumps({
                "region": region,
                "total_returned": len(playlists),
                "playlists": playlists
            }, ensure_ascii=False)
            
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return f"ERROR: {type(e).__name__}: {e}"

@mcp.tool()
async def get_playlist_artists(playlistUrl: str, max: int = 200) -> str:
    """
    Get details of artists featured in a YouTube playlist.
    Args:
      playlistUrl: Full YouTube playlist URL or playlist ID
      max: Max artists to return (1-50)
    """
    logger.info(f"get_playlist_artists called - URL: {playlistUrl}, max: {max}")
    
    try:
        _get_yt_api_key()
    except Exception as e:
        return f"ERROR: {str(e)}"
    
    try:
        # Extract playlist ID from URL
        playlist_id = playlistUrl
        if "youtube.com" in playlistUrl:
            parsed = urlparse(playlistUrl)
            qs = parse_qs(parsed.query)
            if "list" in qs:
                playlist_id = qs["list"][0]
        
        async with httpx.AsyncClient() as client:
            # Get playlist items
            playlist_data = await _yt_get(client, "playlistItems", {
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": max
            })
            
            # Extract unique channel IDs from playlist items
            channel_ids = set()
            for item in playlist_data.get("items", []):
                channel_id = item.get("snippet", {}).get("videoOwnerChannelId")
                if channel_id:
                    channel_ids.add(channel_id)
            
            # Get detailed channel/artist information
            artists = []
            if channel_ids:
                channels_data = await _yt_get(client, "channels", {
                    "part": "snippet,statistics",
                    "id": ",".join(channel_ids)
                })
                
                artists = [_pack_artist(channel) for channel in channels_data.get("items", [])]
                
                # Sort by subscriber count
                artists.sort(key=lambda x: x["subscriberCount"], reverse=True)
            
            logger.info(f"Successfully fetched {len(artists)} artists from playlist")
            
            return json.dumps({
                "playlistId": playlist_id,
                "total_returned": len(artists),
                "artists": artists
            }, ensure_ascii=False)
            
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return f"ERROR: {type(e).__name__}: {e}"

@mcp.tool()
async def get_playlist_details(playlistUrl: str, max_videos: int = 500) -> str:
    """
    Get detailed information about a playlist and its videos.
    Args:
      playlistUrl: Full YouTube playlist URL or playlist ID
      max_videos: Maximum number of videos to return from playlist (1-50)
    """
    logger.info(f"get_playlist_details called - URL: {playlistUrl}, max_videos: {max_videos}")
    
    try:
        _get_yt_api_key()
    except Exception as e:
        return f"ERROR: {str(e)}"
    
    try:
        # Extract playlist ID from URL
        playlist_id = playlistUrl
        if "youtube.com" in playlistUrl:
            parsed = urlparse(playlistUrl)
            qs = parse_qs(parsed.query)
            if "list" in qs:
                playlist_id = qs["list"][0]
        
        async with httpx.AsyncClient() as client:
            # Get playlist details
            playlist_data = await _yt_get(client, "playlists", {
                "part": "snippet,contentDetails,status",
                "id": playlist_id
            })
            
            if not playlist_data.get("items"):
                return "ERROR: Playlist not found"
            
            playlist_info = playlist_data["items"][0]
            
            # Get playlist items (videos)
            videos = []
            page_token = None
            
            while len(videos) < max_videos:
                params = {
                    "part": "snippet,contentDetails",
                    "playlistId": playlist_id,
                    "maxResults": min(50, max_videos - len(videos))
                }
                if page_token:
                    params["pageToken"] = page_token
                
                playlist_items = await _yt_get(client, "playlistItems", params)
                
                video_ids = [item["snippet"]["resourceId"]["videoId"] 
                           for item in playlist_items.get("items", [])]
                
                if video_ids:
                    # Get detailed video information
                    videos_data = await _yt_get(client, "videos", {
                        "part": "snippet,statistics,contentDetails",
                        "id": ",".join(video_ids)
                    })
                    
                    for video in videos_data.get("items", []):
                        videos.append(_pack_video(video))
                
                page_token = playlist_items.get("nextPageToken")
                if not page_token or len(videos) >= max_videos:
                    break
            
            # Pack playlist details
            result = {
                "playlistId": playlist_id,
                "title": playlist_info["snippet"]["title"],
                "description": playlist_info["snippet"]["description"],
                "channelTitle": playlist_info["snippet"]["channelTitle"],
                "channelId": playlist_info["snippet"]["channelId"],
                "publishedAt": playlist_info["snippet"]["publishedAt"],
                "privacyStatus": playlist_info["status"]["privacyStatus"],
                "thumbnails": playlist_info["snippet"]["thumbnails"],
                "videoCount": playlist_info["contentDetails"]["itemCount"],
                "videos": videos,
                "total_videos_returned": len(videos)
            }
            
            logger.info(f"Successfully fetched playlist details with {len(videos)} videos")
            
            return json.dumps(result, ensure_ascii=False)
            
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return f"ERROR: {type(e).__name__}: {e}"

@mcp.tool()
async def search_playlists(query: str, max: int = 50, order: str = "relevance") -> str:
    """
    Search for YouTube playlists by name/query.
    Args:
      query: Search query (e.g., "bollywood hits", "punjabi songs playlist")
      max: Max results to return (1-50)
      order: Sort order - "relevance" (default), "date" (newest), "viewCount" (most viewed), "rating" (highest rated)
    """
    logger.info(f"search_playlists called - Query: {query}, max: {max}, order: {order}")
    
    try:
        _get_yt_api_key()
    except Exception as e:
        return f"ERROR: {str(e)}"
    
    try:
        async with httpx.AsyncClient() as client:
            # Search for playlists
            search_data = await _yt_get(client, "search", {
                "part": "snippet",
                "q": query,
                "type": "playlist",
                "maxResults": max,
                "order": order
            })
            
            playlists = []
            playlist_ids = [item["id"]["playlistId"] for item in search_data.get("items", [])]
            
            if playlist_ids:
                # Get detailed playlist information
                playlist_data = await _yt_get(client, "playlists", {
                    "part": "snippet,contentDetails,status",
                    "id": ",".join(playlist_ids)
                })
                
                for playlist in playlist_data.get("items", []):
                    playlists.append({
                        "playlistId": playlist["id"],
                        "title": playlist["snippet"]["title"],
                        "description": playlist["snippet"]["description"][:200],
                        "channelTitle": playlist["snippet"]["channelTitle"],
                        "channelId": playlist["snippet"]["channelId"],
                        "thumbnails": playlist["snippet"]["thumbnails"],
                        "videoCount": playlist["contentDetails"]["itemCount"],
                        "publishedAt": playlist["snippet"]["publishedAt"],
                        "privacyStatus": playlist["status"]["privacyStatus"],
                        "url": f"https://www.youtube.com/playlist?list={playlist['id']}"
                    })
            
            logger.info(f"Successfully found {len(playlists)} playlists matching query")
            
            return json.dumps({
                "query": query,
                "order": order,
                "total_returned": len(playlists),
                "playlists": playlists
            }, ensure_ascii=False)
            
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return f"ERROR: {type(e).__name__}: {e}"

# ----------------------------
# MAIN — Streamable HTTP on 127.0.0.1:8000/mcp
# ----------------------------
if __name__ == "__main__":
    # Ensure API key is available before the server starts
    try:
        _get_yt_api_key()
        logger.info(f"API Key configured: Yes")
    except Exception as e:
        logger.error(f"Failed to get YouTube API key: {e}")
        sys.exit(1)

   # ✅ Bind to all interfaces and to the platform-provided port
    host = "0.0.0.0"
    port = int(os.getenv("PORT", "8000"))  # FastMCP sets PORT for you

    logger.info(f"Starting YouTube MCP server at http://{host}:{port}/mcp")
    # One endpoint `/mcp` that supports POST (and can stream responses).
    mcp.run(
        "http",               # Streamable HTTP transport (same as Facebook server)
        host=host,
        port=8000,
        path="/mcp"
    )