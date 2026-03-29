"""
Thin wrappers around TikTokApi.
All functions accept an already-initialised api object so a single session
can be reused across multiple calls within one loop iteration.
"""


async def get_user_info(api, username: str) -> dict:
    """Fetch user profile data. Returns a normalised dict."""
    user = api.user(username=username)
    data = await user.info()
    u = data.get("userInfo", {}).get("user", {})
    s = data.get("userInfo", {}).get("stats", {})

    if not u.get("id"):
        raise ValueError(f"No user data returned for @{username}")

    return {
        "tiktok_id":      u.get("id"),
        "username":       u.get("uniqueId", username),
        "display_name":   u.get("nickname"),
        "bio":            u.get("signature"),
        "join_date":      u.get("createTime"),
        "follower_count": s.get("followerCount", 0),
        "following_count": s.get("followingCount", 0),
        "video_count":    s.get("videoCount", 0),
        # 'secret' flag is set on private / banned accounts
        "account_status": "banned" if u.get("secret") else "active",
    }


async def get_user_videos(api, username: str, count: int = 3000) -> list[dict]:
    """Fetch all visible videos from a user's profile."""
    videos = []
    async for video in api.user(username=username).videos(count=count):
        d = video.as_dict
        videos.append({
            "video_id":    d.get("id"),
            "description": d.get("desc", ""),
            "upload_date": d.get("createTime"),
            "type":        "photo" if d.get("imagePost") else "video",
        })
    return videos
