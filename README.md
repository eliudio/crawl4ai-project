# Name
* böt.com
* doubledotbot.com
* 2dotbot.com
* twodotbot.com
* frøya.com
* bọt.com
* bõt.com
* hobblez.com

# What?
1. Race db. Find a race
2. Booking.com for races.
   * (small, local) organizers can create a race
   * no commission
   * members can book to join
   * some races are only references
3. History of races you've done, with time, ... Upload photos, ... Link to Strava / Garmin
4. Community of people
5. Map overview, with races, flags where you've ran, where other's ran, ...

# How
1. Scrape internet / organizers. 
* host firecrawl locally
* use grok for AI

Run process_specified_sites.py: to process preconfigured sites
Run process_sites.py: to create the site config for scraping by grok, then process that site

2. Feed from Strava, to recognize which race you ran
3. Flutterflow
4. Stripe pay direct to the customer

# Legal aspects
1. Full database copy isn't ideal, so exclude aggregators
2. Potentially request organizers if ok
3. Check robots.txt / TOS

# TODO:
1. Add type of race, length of race
2. Add frequency: yearly, monthly, weekly, daily, single event
2. Instead of a hard coded list of SiteConfig: Ask grok to generate this SiteConfig for a URL, rather than have it in the code. Store that in a database and then use it.
   THIS IS THE CHANGE I CURRENTLY HAVE AND NOT YET COMMITTED.
   IT DOESN'T WORK YET
3. Ability to rerun store_details from the md in the database, without scraping it.

# COLLECT DATA:
## Aggregators
Retrieve organisers from aggregators.

### Done:
* findarace.com

### To consider:
* runningcalendar.co.uk
* englandathletics.org/runevents/
* runabc.co.uk
* running.org 
* etchrock.com
* racedirectorshq.com/gb/directory/

* Trail Running Association - https://www.tra-uk.org/
* Centurion Running - https://www.centurionrunning.com/
* Ultra X - https://ultra-x.co/
* British Triathlon - https://www.britishtriathlon.org/
* Challenge Family - https://www.challenge-family.com/
* IRONMAN - https://www.ironman.com/
* Spartan Race (UK) - https://uk.spartan.com/en/
* Tough Mudder (UK) - https://uk.toughmudder.com/
* The Wolf Run - https://www.thewolfrun.com/

## Organisers
Collect races from organisers

### Organisers found on findarace.com

1,A.S.O. UK,https://aso-uk.com/
1,Active South West Ltd,https://www.activesouthwest.co.uk/
1,Alcohol Change UK,https://alcoholchange.org.uk/
1,Balmoral Road Races Ltd,https://runbalmoral.com/
1,Basingstoke and Mid Hants Athletics Club,https://bmhac.co.uk/
1,Beacon Race Events,https://www.beaconrace-events.co.uk/
1,Bedford Harriers,https://www.bedfordharriers.co.uk/
1,Belvoir Challenge (parents and friends of Harby Primary School),https://belvoirchallenge.com
1,Bog Dog Running,https://www.bog-dog.co.uk/
1,Castle Race Series,https://www.castleraceseries.com/
1,Challenge Running Ltd,https://www.challenge-running.co.uk/
1,Colchester10k / Rotary in Colchester,https://colchester10k.com/
1,Dartmoor Marathon,https://www.dartmoormarathon.co.uk/
1,Dave Talbot - Adventure Events,https://davetalbot.net/
1,Dirt Running,https://dirtrunning.org.uk/
1,Do3,https://www.do3.co.uk/
1,Ealing Half Marathon,https://www.ealinghalfmarathon.com/
1,Epic Endurance,https://www.epicenduranceevents.co.uk/
1,Eyam Half Marathon,https://www.eyamhalfmarathon.org/
1,Focal Events,https://focal.events/
1,Foxes Farm Produce,https://foxesfarmproduce.co.uk/
1,Frimley Health Charity,https://www.frimleyhealthcharity.org/
1,Frome Triathlon Club,https://clubspark.net/frometriathlonclub
1,Goring 10K,https://www.goring10k.com
1,Gravel and Grit Events
1,Great Run,https://www.greatrun.org
1,Haydon Bridge & Haydon Parish Development Trust Ltd.,https://haydon-bridge.co.uk/
1,Hellfire Events Ltd,https://www.hellfireevents.com/
1,Howling Events Ltd.
1,Insane Terrain Running,https://www.insaneterrainevents.com/
1,Jurassic Coast 10K,https://jurassiccoast10k.co.uk/
1,Lechlade & District Lions Club,https://www.lechladelions.org.uk/
1,Little Lifts,https://www.littlelifts.org.uk/
1,London Marathon Events,https://www.londonmarathonevents.co.uk/
1,MG SPORT
1,MK Marathon Weekend,https://mkmarathon.com/
1,MOAR Events Ltd
1,Nene Valley Races,https://nenevalleyraces.com/
1,Nice Work Partner Races / Wadhurst Runners,https://www.nice-work.org.uk/ (event host: https://www.wadhurstrunners.com/)
1,Nice Work Partner Races on behalf of the Romney Marsh Rotary Club,https://www.nice-work.org.uk/ (rotary club: https://romneymarshrotary.co.uk/)
1,One More Lap
1,Phoenix Running Crystal Palace,https://www.phoenixrunning.co.uk/
1,Redway Runners,https://www.redwayrunners.com/
1,RTC Events,https://www.rtcevents.co.uk/
1,Runningmonk Trail Events,https://www.runningmonktrailevents.com/
1,Sporting Events UK,https://www.sportingeventsuk.com/
1,Street Child,https://street-child.org/
1,Stuweb Events,https://www.stuweb.co.uk/
1,Superhuman Sports,https://www.superhumansports.com/
1,Surrey Trek and Run,https://www.surreytrekandrun.co.uk/
1,Teach First,https://www.teachfirst.org.uk/
1,The Amazing Northampton Run CIC,https://www.theamazingnorthamptonrun.co.uk/
1,The Robin Cancer Trust,https://www.therobincancertrust.org/
1,The Royal Parks charity,https://www.royalparks.org.uk/ (event: https://www.royalparkshalf.com/)
1,Three Forts Challenge,https://www.threefortschallenge.org.uk/
1,Turner Swim,https://www.turnerswim.co.uk/
1,UK TRAINING CLUB
1,VRM TEAM ASD
1,Wilderness Development,https://www.wilderness-development.com/
1,WitchWood Run,https://witchwoodrun.com/
1,Worcester Triathlon Club,https://worcestertriclub.co.uk/
2,Challenging Events
2,Geo-Planet Wildmarathon,https://wildmarathon.com/
2,Hare & Tortoise Running Ltd,https://www.hareandtortoiserunning.co.uk/
2,KS-Client Events
2,Limelight Sports,http://limelightsportsgroup.com/
2,Northstowe Running Festival Events,https://northstowerunfest.co.uk/
2,Phoenix Running Bedfordshire,https://www.phoenixrunning.co.uk/
2,Prosper Events,https://www.prosperevents.co.uk/
2,Rory Macpherson
2,Run Fanatics,https://runfanatics.com/
2,Run Jersey Events,https://www.runjersey.co.uk/
2,Run Rugged Events
2,The Drop,https://www.thedropuk.co.uk/
2,TriBourne Multisport Events,https://www.tribourne.co.uk/
2,Trident Sports Events,https://www.tridentsportsevents.co.uk/
2,Ultra Violet Ltd
2,votwo events,https://www.votwo.co.uk/
2,Well Run
2,Wild Running Ltd,https://www.wildrunning.co.uk/
2,X-TRON Live,https://x-tronlive.com/
3,Andali Events,https://www.andalievents.com/
3,Barking Mad Events,https://www.barkingmadevents.com/
3,EndorphinSport Ltd,https://www.endorphinsport.com/
3,Epic Endurance Events CIC,https://www.epicenduranceevents.co.uk/
3,Future Sports Events Ltd,https://futuresportsevents.com/
3,GBR Run,https://www.gbrrun.com/
3,krono:sports,https://www.kronosports.uk/
3,Outsider Events,https://www.outsiderevents.com/
3,Pacesetter Events,https://www.pacesetterevents.com/
3,Phoenix Running West Sussex,https://www.phoenixrunning.co.uk/
3,RaceNation Events,https://racenationevents.com/
3,Raceways Events,https://findarace.com/racewaysevents
3,Resolute Running,https://www.resoluterunning.co.uk/
3,Roy Castle Lung Cancer Foundation,https://roycastle.org/
4,Action Medical Research,https://action.org.uk/
4,Active Leisure Events Ltd,https://www.activeleisureevents.co.uk/
4,Barnes Fitness,https://www.barnesfitness.co.uk/
4,CIRCUIT RUNNING EVENTS LIMITED,https://www.circuitrunning.co.uk/
4,Dynamic Adventures,https://dynamicadventurescic.co.uk/our-events
4,Eventrex,https://eventrexuk.com/
4,HermesRunning,https://www.hermesrunning.com/
4,Phoenix Running Hampshire,https://www.phoenixrunning.co.uk/
4,Ridge Runners,https://www.ridgerunners.co.uk/
4,Running Tribe,https://runningtribe.co.uk/
4,Sandford Parks Lido,https://www.sandfordparkslido.org.uk/
5,Activity Wales Events Limited,https://www.activitywalesevents.com/
5,BustinSkin Events,https://bustinskin.com/
5,Events of the North,https://eventsofthenorth.com/
5,Ryde Harriers,https://www.rydeharriers.co.uk/
5,Trail Blazing Events,https://www.trailblazingevents.co.uk/
5,UK Triathlon,https://uktriathlon.co.uk/
6,Endurotrek,https://www.endurotrek.co.uk
6,EnduroTrek,https://www.endurotrek.co.uk
6,endurotrek,https://www.endurotrek.co.uk
6,Every Mile Counts
6,Good Running Events,https://www.goodrunningevents.co.uk/
6,Long Player Running,https://www.longplayerrunning.co.uk/
6,OuterEdge Events,https://outeredge-events.com/
6,RUN-FEST.com,https://www.run-fest.com/
7,Bridge Events Dartford,https://www.bridgetriathlon.co.uk/
7,endurosport LLP
7,Go Beyond,https://www.gobeyond.org.uk/
8,Phoenix Running Cambridgeshire,https://www.phoenixrunning.co.uk/
8,Race Harborough,https://raceharborough.co.uk/
8,Runaway Racing,https://runawayracing.com/
9,BigFeat Events,https://bigfeatevents.com/
9,Nice Work Partner Races,https://www.nice-work.org.uk/
9,Phoenix Running Manchester,https://www.phoenixrunning.co.uk/
9,Racing Line Running,https://www.racinglinerunning.co.uk/
9,UK Cycling Events
9,Ultra X,https://ultra-x.co/
10,Always Aim High Events,https://alwaysaimhighevents.com/
10,OP Events
10,Out-Fit Events,https://out-fit.co.uk/
11,Big Bear Events
11,Run UK,https://www.runuk.co.uk/
11,Secret London Runs,https://www.secretlondonruns.com/
12,Letsgovelo,https://www.letsgovelo.co.uk/
12,Phoenix Running South Wales,https://www.phoenixrunning.co.uk/
12,Relish Running races,https://www.relishrunningraces.com/
12,Sri Chinmoy Tri,https://uk.srichinmoyraces.org/ (club: https://clubspark.net/SriChinmoyTriathlonClub)
13,Enigma Running,http://www.enigmarunning.co.uk/
13,The Clubhouse
14,MCC Promotions,https://www.mccpromotions.com/
14,Run the Wild,https://runthewild.co.uk/
15,Running Adventures,https://www.runningadventures.uk/
15,Ultra Running Ltd,https://www.ultrarunningltd.co.uk/
16,Fylde Coast Runners,https://www.fyldecoastrunners.com/
16,Up and Running Events,https://www.upandrunningevents.co.uk/
18,GSi Events Ltd,https://www.gsi-events.com/
18,Run Nation,https://www.runnation.co.uk/
19,Rasselbock Running,https://rasselbock.co.uk/
21,Nice Work,https://www.nice-work.org.uk/
24,Running Events Devon,https://runningeventsdevon.co.uk/
25,Onerace Events,https://www.onerace.events/
25,The Fix Events UK,https://thefixevents.com/
26,Sportiva Events,CODED,https://sportivaevents.co.uk/events/
28,It's Grim up North Running, CODED,https://www.itsgrimupnorthrunning.co.uk/
33,Zig Zag Running, CODED,https://www.zigzagrunning.co.uk/
35,Phoenix Running, CODED,https://www.phoenixrunning.co.uk/
35,UK Running Events, CODED,https://www.ukrunningevents.co.uk/
44,ATW, CODED,https://www.atwevents.co.uk/
60,Saturn Running, CODED,https://www.saturnrunning.co.uk/
235,RunThrough Events, CODED,https://www.runthrough.co.uk/
295,Cancer Research UK - Race for Life, CODED,https://raceforlife.cancerresearchuk.org/

### Collect organizers from https://racecheck.com/

Not in above list:

AAT Events,https://aat-events.com/
Bassetlaw Triathlon Club,https://www.bassetlawtriclub.co.uk/
Cardiff Triathletes,https://www.cardifftri.net
Club La Santa,https://www.clublasanta.com/
Commando Series,https://www.commandoseries.co.uk/
Cotswold Running,https://cotswoldrunning.co.uk/
Crystal Palace Tri Club,https://crystalpalacetriathletes.com/
Everyone Active,https://www.everyoneactive.com/
F3 Events,https://www.f3events.co.uk/
Hereford Triathlon Club,https://www.herefordtriathlonclub.co.uk/
HUUB,https://huubdesign.com/
Ironman,https://www.ironman.com/
Mad Hatters,https://www.madhattersportsevents.co.uk/
Mid Sussex Tri Club,https://www.midsussextriclub.com/
Newbury Athletic Club,https://www.newburyac.org.uk/
NYP Tri,https://www.nyptristars.co.uk/
OSB Events,https://www.osbevents.com/
Portsmouth Triathletes,https://portsmouthtriathletes.co.uk/
Rocket Race
Rough Runner,https://roughrunner.com/
Run North West,https://www.runnorthwest.co.uk/
See York Run York,https://www.seeyorkrunyork.co.uk/
Sunderland Strollers,https://sunderlandstrollers.co.uk/
SY Tri,https://sytri.org/
Wilmslow Running Club,https://wilmslowrunningclub.co.uk/
York Knavesmire Harriers,https://www.yorkknavesmireharriers.co.uk/
Spartan Race,https://uk.spartan.com/en/
Rock 'n' Roll Running Series,https://www.runrocknroll.com/
Threshold Sports,https://www.thresholdsports.co.uk/
Rat Race Events,https://www.ratrace.com/
Curley's Leisure / The ROC Triathlon
White Star Running,https://whitestarrunning.co.uk/
Centurion Running,https://www.centurionrunning.com/
Let’s Do This,https://www.letsdothis.com/gb
Challenge Family,https://www.challenge-family.com/
XTERRA,https://www.xterraplanet.com/
The Wolf Run,https://www.thewolfrun.com/
Maverick Race,https://www.maverick-race.com/
Wild Deer Events,https://www.wilddeerevents.co.uk/

### Etc
...