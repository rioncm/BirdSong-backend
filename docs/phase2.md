# Phase 2 Data completion

# To be completed

## Entries in the species table

Table Model below for reference "*" indicates data missing from entries

 ``` python
 species = Table(
    "species",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("sci_name", String(255), nullable=False, unique=True),
   * Column("species", String(255)),
   *  Column("genus", String(255)),
   *  Column("family", String(255)),
    Column("common_name", String(255)),
   * Column("first_id", DateTime),
   * Column("last_id", DateTime),
   * Column("image_url", String(512)),
   * Column("id_days", Integer, default=0),
   * Column("info_url", String(512)),
   * Column("summary", Text),
)
```
### what is needed
**species**  from the GBIF lookup
**genus** from the GBIF lookup
**family**  from the GBIF lookup
**first_id** date and time stamp for the first ID
**last_id** date and time stamp the last time this species was id'd
**image_url** -- See below
**id_days** count of the days this species has been id'd only increments when last_id is being update and last_id before update is <= prior day midnight
**info_url** -- see below
**ebird_code** direct linking code from eBird taxonomy
**summary** 
    1. column name changed from ai_summary to summary, needs migration
    2. scrape summary from eBird website from the identification section. **see below**
### image_url 

A representitive image of the species should be download on first id using the wikicommons datasource. 
The image should be stored on the backend and served to the frontend timeline for display in a detection card. 
Attribution should be stored is the data_citations table properly typed and keyed. 
The cached image should have the species is prepended to the file name to make manual id of the file easier.  

### Clarifying questions
- Q: Should we prefer a specific image size or aspect ratio from Wikimedia’s media API (e.g., always request the `medium` rendition) before saving locally?
    - A: medium is perfect. 
- Q: Where should the downloaded images live on disk (existing `images/` directory under `data/`, or a dedicated `species/` subfolder)?
    - A: Images will not be distributed with the project. Docker will map in a directory under app for images. You can use the current images folder under app for the target location. 
- Q: If Wikimedia doesn’t return an image, do we fall back to iNaturalist/Macaulay or leave `image_url` empty for now?
    - A: Leave it empty
- Q: Do we need to version cached images, or is overwriting the file on re-enrichment acceptable?
    - A: The once a species is enriched and added to the database the picture is immutable via the enrichment process. In a later version an admin will be able to change the image but that is not to be addressed at the moment.  

### info_url

I have added the API key for eBird it info URL should be derived from a call to the API to 
https://api.ebird.org/v2/ref/taxonomy/ebird

the response includes SPECIES_CODE=thagul which allows for direct linking to 

https://ebird.org/species/SPECIES_CODE

**NOTE:** Stick with GBIF for taxa data to allow for other users to run the project without getting an eBird API key. 


# Completed
- Added `backend/app/backfill_species_enrichment.py` to re-run enrichment for existing species rows. Invoke with `python backend/app/backfill_species_enrichment.py` (use `--dry-run` first) after updating code so legacy entries pick up genus/family/species, summaries, and image metadata.
- Species enrichment now initializes the eBird client when an API key is provided and stores the returned `ebird_code` alongside each species for direct linking.
- Wikimedia enrichment now uses the Commons REST `/search/page` + `/file/{key}` workflow and caches the `preferred.url` rendition with proper attribution details.
