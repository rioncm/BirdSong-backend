# Planed Features

## Backend
- capture audio from Unifi RTSP stream
- analyze through BirdNet models
- log captures to database
    - include file path to wav file
- discard files without matches
- notify all first detections
- notify on configured long interval between detection (>2 months)
- notify channels
    - telegram
    - SMS
- detection cleanup
    - if multiple species are detected in the same audio clip which are of the same geuns only the highest confidence is logged
    
- normalize id results to estimate count of id's



## Front end
- Scrolling id viewer with bird pictures
    - link to more data from image
- date / time selector
- user specified alerts for species 
