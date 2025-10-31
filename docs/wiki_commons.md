# Wikimedia REST API Documentation

https://www.mediawiki.org/wiki/API:REST_API/Reference#


# Wikimedia Commons
**base url** https://commons.wikimedia.org/w/rest.php/v1

## Search Endpoint
GET /search/page
Parameters q=SEARCH_TERM&limit=INT
Example https://commons.wikimedia.org/w/rest.php/v1/search/page?q=Yellow-rumped Warbler&limit=5

## Returns

``` json
{
    "pages": [
        {
            "id": 147433618,
            "key": "File:Yellow-rumped_warbler_singing_(41612).jpg",
            "title": "File:Yellow-rumped warbler singing (41612).jpg",
            "excerpt": "0 Creative Commons Attribution-Share Alike 4.0 truetrue English <span class=\"searchmatch\">Yellow</span>-<span class=\"searchmatch\">rumped</span> <span class=\"searchmatch\">warbler</span> male singing author name string: Rhododendrites Wikimedia username:",
            "matched_title": null,
            "anchor": null,
            "description": null,
            "thumbnail": {
                "mimetype": "image/jpeg",
                "width": 60,
                "height": 48,
                "duration": null,
                "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/aa/Yellow-rumped_warbler_singing_%2841612%29.jpg/60px-Yellow-rumped_warbler_singing_%2841612%29.jpg"
            }
        },...
```

### Key Information
pages[x].key -- File name for secondary search
pages[x].excerpt -- license


## Files Endpoint
GET /file/FILE_KEY  ***From*** pages[INDEX)].key
File:Yellow-rumped_warbler_singing_(41612).jpg
Example https://commons.wikimedia.org/w/rest.php/v1/file/File:Yellow-rumped_warbler_singing_(41612).jpg

### Returns

``` json
{
    "title": "Yellow-rumped warbler singing (41612).jpg",
    "file_description_url": "//commons.wikimedia.org/wiki/File:Yellow-rumped_warbler_singing_(41612).jpg",
    "latest": {
        "timestamp": "2024-04-16T11:35:35Z",
        "user": {
            "id": 1966889,
            "name": "Rhododendrites"
        }
    },
    "preferred": {
        "mediatype": "BITMAP",
        "size": null,
        "width": 757,
        "height": 600,
        "duration": null,
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/aa/Yellow-rumped_warbler_singing_%2841612%29.jpg/960px-Yellow-rumped_warbler_singing_%2841612%29.jpg"
    },
    "original": {
        "mediatype": "BITMAP",
        "size": 6471441,
        "width": 3918,
        "height": 3104,
        "duration": null,
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/aa/Yellow-rumped_warbler_singing_%2841612%29.jpg"
    },
    "thumbnail": {
        "mediatype": "BITMAP",
        "size": null,
        "width": 2560,
        "height": 2028,
        "duration": null,
        "url": "https://upload.wikimedia.org/wikipedia/commons/thumb/a/aa/Yellow-rumped_warbler_singing_%2841612%29.jpg/2560px-Yellow-rumped_warbler_singing_%2841612%29.jpg"
    }
}
```

### Key Information
preferred.url -- URL of image to download
title.latest.user.name -- user name to attribute
title.latest.user.id -- for link building attribution

### Integration Notes
- Image selection now uses `/search/page` to find the best matching file and `/file/{key}` to pull the preferred rendition and author metadata.
- The enrichment pipeline caches the `preferred.url` image locally (falling back to `original.url` when needed) and stores both the uploader name and a direct profile link for attribution.
