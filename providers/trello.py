import csv
from datetime import datetime
import dateutil.parser as dateparser
import decimal
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
api_token = config['trello']['api_token']

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
          (path, api_key, api_token)

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
                         'moveCardFromBoard,moveCardToBoard,' +
                         'addMemberToCard,removeMemberFromCard')

# Sort events in the timeline by card id, then event date
timeline = sorted(timeline, key=lambda event: (event['data']['card']['id'],
                                               event['date']))

print "\nTimeline:"

for event in timeline:
    card = event['data']['card']
    print "   %s | %s | %s" % (card['name'].encode('ascii', 'ignore'),
                               event['date'], event['type'])

# ========================
# CACHE SELECTED CARD DATA
# ========================

card_data = []
list_data = {}

for board in boards:
    card_data += get_json('/boards/%s/cards' % board['id'],
                          'fields=idList,labels')
    for item in get_json('/board/%s/lists' % board['id']):
        list_data[item['id']] = item


current_list_of = {}
current_labels_of = {}

for item in card_data:
    current_list_of[item['id']] = list_data[item['idList']]
    current_labels_of[item['id']] = item['labels']

# ===========================================
# CALCULATE TIME SPENT BY A CARD IN EACH LIST
# ===========================================

print "\nTime spent in each list:"

time_spent_in_list = []

# Used throughout the loop to determine who are the members of current card
members = []

for index in range(0, len(timeline)):
    event = timeline[index]
    card = event['data']['card']

    # Look back to the previous event
    if index == 0:
        prev_card = None
    else:
        prev_card = timeline[index - 1]['data']['card']

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

    # Determine who are the members of the current card
    if event['type'] in ['addMemberToCard', 'removeMemberFromCard']:
        if event['type'] == 'addMemberToCard' and (prev_card is None
           or prev_card['id'] != card['id']):
            # Start of timeline or we've moved to another card
            members = [event['member']]

        elif event['type'] == 'addMemberToCard' and \
                prev_card['id'] == card['id'] and \
                event['member']['id'] not in [m['id'] for m in members]:
            # Same card but new event
            members.append(event['member'])

        elif event['type'] == 'removeMemberFromCard':
            # We don't care where we are in the timeline. Just remove
            # that member from the members list.
            members = [m for m in members if m['id'] != event['member']['id']]

        # We don't need anything else from this
        # event. Move on to the next one
        continue

    # This event is redundant. Its corresponding 'moveCardToBoard' will do.
    # So let's skip ahead to the next event.
    if event['type'] == 'moveCardFromBoard':
        continue

    # TODO: There are instances when the first item is a moveCardFromBoard.
    # Handle that here!

    datetime_in = dateparser.parse(event['date'])

    # Based on what's next, determine when the card left the list
    # as well as compute the time the card spent on that list
    if next_event and next_card['id'] == card['id']:
        datetime_out = dateparser.parse(next_event['date'])
        duration = datetime_out - datetime_in
    else:
        datetime_out = "N/A"
        duration = utc_now - datetime_in

    duration = "{:.0f}".format(duration.total_seconds())

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
        # The event doesn't have the card's list information. Let's
        # assume it's the current list where the card is in.
        card_list = current_list_of[card['id']]

    # If it's currently in the 'Done' list and it never left,
    # then it was moved to done correctly. Don't count its time.
    if card_list['name'] in config['trello']['list_name_types']['done'] \
       and datetime_out == 'N/A':
        duration = ''

    card_name = card['name']
    card_name = (card_name[:30].strip() + '...') \
        if len(card_name) > 30 else card_name

    print "   %s %s | %s:%s | time: %s" % \
          (card['id'], card_name, event['data']['board']['name'],
           card_list['name'], duration)

    # Convert times to local time for display
    # TODO: Make the local tz configurable
    local_tz = pytz.timezone(config['common']['local_timezone'])

    local_datetime_in = datetime_in.astimezone(
        local_tz).strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(datetime_out, basestring):
        local_datetime_out = datetime_out
    else:
        local_datetime_out = datetime_out.astimezone(
            local_tz).strftime("%Y-%m-%d %H:%M:%S")

    if card['id'] in current_labels_of.keys():
        labels = current_labels_of[card['id']]
    else:
        labels = []

    # Extract the story points, if any
    match = re.match("\((\d+\.?\d*)\) ?(.*)", card['name'])
    if match is None:
        story_points = ''
        card_name = card['name']
    else:
        story_points = match.group(1)
        card_name = match.group(2)

    # Finally, record the data
    time_spent_in_list.append({
        'card_id': card['id'],
        'card_name': card_name,
        'board_name': event['data']['board']['name'],
        'list_name': card_list['name'],
        'datetime_in': local_datetime_in,
        'datetime_out': local_datetime_out,
        'duration': duration,
        'members': members,
        'labels': labels,
        'story_points': story_points})

# ============
# WRITE TO CSV
# ============

csvname = "cards-%s.csv" % utc_now.strftime("%Y-%m-%d-%H%M%S")
csvpath = path.abspath(path.join(__file__, '..', '..', csvname))

with open(csvpath, 'wb') as csvfile:
    writer = csv.writer(csvfile, delimiter=',', quoting=csv.QUOTE_MINIMAL)
    local_time_now = utc_now.astimezone(local_tz).strftime("%Y-%m-%d %H:%M:%S")
    writer.writerow(["As of %s (%s)" % (local_time_now, local_tz),
                    "", "", "", "", "", "", "", "", ""])
    writer.writerow(["Card ID", "Card Name", "Points", "Labels",
                     "Members", "Board", "List",
                     "In (%s)" % local_tz,
                     "Out (%s)" % local_tz,
                     "Duration (Seconds)"])

    for row in time_spent_in_list:
        r = [row['card_id'], row['card_name'].encode('utf-8'),
             row['story_points'],
             ', '.join([l['name'] for l in row['labels']]),
             ', '.join([m['fullName'] for m in row['members']]),
             row['board_name'], row['list_name'], row['datetime_in'],
             row['datetime_out'], row['duration']]

        writer.writerow(r)
