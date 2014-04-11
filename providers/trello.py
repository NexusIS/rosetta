import csv
from datetime import datetime
import dateutil.parser as dateparser
import json
import os.path as path
import pytz
import re
import urllib2
import yaml

cfgfile_path = path.abspath(path.join(__file__, '..', '..', 'config.yml'))
with open(cfgfile_path, 'r') as cfgfile:
    config = yaml.load(cfgfile)

name_patterns = config['trello']['board_name_patterns']
api_key = config['trello']['api_key']
api_secret = config['trello']['api_secret']

print """
         {
      {   }
       }_{ __{
    .-{   }   }-.
   (   }     {   )
   |`-.._____..-'|
   |             ;--.
   |            (__  \\
   |             | )  )
   |             |/  /
   |             /  /
   |            (  /
   \             y'
    `-.._____..-'
"""


def get_json(path, params=None):
    url = "https://trello.com/1%s?limit=1000&key=%s&token=%s" % \
          (path, api_key, api_secret)

    if params is not None:
        url += ("&%s" % params)

    return json.loads(urllib2.urlopen(url).read())

# For use later in the script. We want all ongoing cards
# to use this as a reference current time instead of getting
# a new utcnow() for each loop iteration otherwise cards processed
# later in the loop will have a longer 'time spent' than cards
# processed earlier in the loop
utc_now = datetime.utcnow()
utc_now = utc_now.replace(tzinfo=pytz.utc)

boards = get_json('/members/my/boards')
sprints = []

# ===========================
# MAP OUT ALL RELEVANT BOARDS
# ===========================

# Process the boards for sorting
for board in boards:
    # Capture the product backlog
    match = re.match(name_patterns['product_backlog'], board['name'])
    if match:
        product_backlog = board
        next

    # Capture the sprint backlogs
    match = re.match(name_patterns['sprint_backlog'], board['name'])
    if match:
        sprint = board
        # Get the board number from the name and padd it with zeros
        # We will use this for correctly sorting sprints below
        sprint['sprint_number'] = match.group(1).zfill(4)
        sprints.append(sprint)

# Sort boards correctly
boards = sorted(sprints, key=lambda sprint: sprint['sprint_number'])
boards.insert(0, product_backlog)

print "Found Boards:"

for board in boards:
    print "   %s %s" % (board['id'], board['name'])

# ================================
# COLLATE ALL EVENTS IN ALL BOARDS
# ================================

timeline = []

for board in boards:
    timeline += get_json('/boards/%s/actions' % board['id'],
                         'filter=createCard,updateCard:idList,' +
                         'moveCardFromBoard,moveCardToBoard')

# Sort events in the timeline by card id, then event date
timeline = sorted(timeline, key=lambda event: (event['data']['card']['id'],
                                               event['date']))

print "\nTimeline:"

for event in timeline:
    card = event['data']['card']
    print "   %s | %s | %s" % (card['name'], event['date'], event['type'])

# ===========================================
# CALCULATE TIME SPENT BY A CARD IN EACH LIST
# ===========================================

print "\nTime spent in each list:"

time_spent_in_list = []

# This will serve as cache for the current list of a card
current_list_of = {}

for index in range(0, len(timeline)):
    event = timeline[index]
    card = event['data']['card']

    # This event is redundant. Its corresponding 'moveCardToBoard' will do.
    # So let's skip ahead to the next event.
    if event['type'] == 'moveCardFromBoard':
        continue

    # TODO: There are instances when the first item is a moveCardFromBoard.
    # Handle that here!

    datetime_in = dateparser.parse(event['date'])

    # Take a peek at the next event
    if event['type'] == 'moveCardToBoard' and index < len(timeline) - 2:
        # A moveCardToBoard event happens when a card is moved into the board.
        # The event following it is always the corresponding moveCardFromBoard
        # of the other board which happens just a few milliseconds after
        # moveCardToBard. This will result in a near-zero time-span for our
        # moveCardToBoard. So let's look two events ahead for guidance.
        next_event = timeline[index + 2]
        next_card = next_event['data']['card']
    elif event['type'] != 'moveCardToBoard' and index < len(timeline) - 1:
        next_event = timeline[index + 1]
        next_card = next_event['data']['card']
    else:
        next_event = next_card = None

    # Based on what's next, determine when the card left the list
    # as well as compute the time the card spent on that list
    if next_event and next_card['id'] == card['id']:
        datetime_out = dateparser.parse(next_event['date'])
        total_time = datetime_out - datetime_in
    else:
        datetime_out = "N/A"
        total_time = utc_now - datetime_in

    # SANITIZE A createCard EVENT'S BOARD INFORMATION
    # If the current event is a 'createCard' and the respective card was
    # moved further down in the timeline, then the board information in the
    # current event is wrong! Sanitize it by looking at the next event (make
    # sure it's still an event of the current card!) and using the board
    # information there.
    if event['type'] == 'createCard' \
       and next_event \
       and next_card['id'] == card['id']:

        if next_event['type'] == 'moveCardToBoard':
            event['data']['board'] = next_event['data']['boardSource']
        else:
            event['data']['board'] = next_event['data']['board']

    # Determine in which list this event happened
    if 'listAfter' in event['data'].keys():
        card_list = event['data']['listAfter']
    elif 'list' in event['data'].keys():
        card_list = event['data']['list']
    else:
        # The event doesn't have the card's list information.
        # Let's assume it's the current list where the card is
        # in. We can query that via /cards/[card id]/list but
        # first check if we've already done that by inspecting
        # our cache of current lists.
        if card['id'] not in current_list_of.keys():
            current_list_of[card['id']] = \
                get_json('/cards/%s/list' % card['id'])

        card_list = current_list_of[card['id']]

    # If it's currently in the 'Done' list and it never left,
    # then it was moved to done correctly. Don't count its time.
    if card_list['name'] in config['trello']['list_name_types']['done'] \
       and datetime_out == 'N/A':
        total_time = 'N/A'

    card_name = card['name']
    card_name = (card_name[:30].strip() + '...') \
        if len(card_name) > 30 else card_name

    print "   %s %s | %s:%s | time: %s" % \
          (card['id'], card_name, event['data']['board']['name'],
           card_list['name'], total_time)

    time_spent_in_list.append({
        'card_id': card['id'],
        'card_name': card['name'].strip(),
        'board_name': event['data']['board']['name'],
        'list_name': card_list['name'],
        'datetime_in': datetime_in,
        'datetime_out': datetime_out,
        'total_time': total_time})

# ============
# WRITE TO CSV
# ============

csvname = "cards-%s.csv" % utc_now.strftime("%Y-%m-%d-%H%M%S")
csvpath = path.abspath(path.join(__file__, '..', '..', csvname))

with open(csvpath, 'wb') as csvfile:
    writer = csv.writer(csvfile, delimiter=',', quoting=csv.QUOTE_MINIMAL)
    writer.writerow(["As of %s" % utc_now, "", "", "", "", "", ""])
    writer.writerow(["Card ID", "Card Name", "Board", "List",
                     "In", "Out", "Time In List"])

    for row in time_spent_in_list:
        r = [row['card_id'], row['card_name'].encode('utf-8'),
             row['board_name'], row['list_name'], row['datetime_in'],
             row['datetime_out'], row['total_time']]

        writer.writerow(r)
