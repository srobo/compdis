#!/usr/bin/env python
import redis
import json
import sys
import scheduler # there's lots we can reuse here. :)

"""
This script will schedule the finals, which are structured as detailed below,

usage: schedule-finals takes no arguments nor options. It will determine how many knockout (final)
matches have been scheduled thus far (org.srobo.matches.knockout_matches_scheduled),
and politely schedule the next set.

If there is no next set, you will be informed of that as well. ;)

Should you need to reschedule some set of matches, set org.srobo.matches.knockout_matches_scheduled
to 0, 4, or 6 depending on whether you want to reschedule the quarter-finals, semi-finals or final respectively.

NB: THIS SCRIPT DEPENDS ON NO MATCHES BEING SCHEDULED EXCEPT FINALS ONCE THE FINALS HAVE STARTED!!
(ie, it expects that the last N (N <= 7) items in org.srobo.matches are finals matches)

----------------

Each match takes has 8 minutes, of which 7 are structured as with league matches.
The spare minute will be placed between matches (such that there is a minute of "downtime" between
consecutive final matches
16 top teams (in terms of league points) to play

(4 4-robot matches) 
A: 1, 5, 9, 13
B: 2, 6, 10, 14
C: 3, 7, 11, 15
D: 4, 8, 12, 16

(where 1 is the highest scoring team and 16 is the lowest scoring team of the teams that got in)

the top two from each match then play

(2 4-robot matches)
E: top two of A vs top two of B
F: top two of C vs top two of D

the top two from each match then play THE FINAL

G ("FINAL"): top two of E and F (1 4 robot match)

"""

MATCH_ZONES = 4 # you never know

r = redis.Redis(host='localhost', port=6379, db=0)

def AppendToMatches(matches):
  """
  appends the given list of matches (as python dicts) to org.srobo.matches
  """
  match_strings = []
  
  for m in matches:
    match_strings.append(scheduler.match_to_ms(m))
    
  for m_str in match_strings:
    r.rpush("org.srobo.matches", m_str)

def GetGameScore(tla, match_no):
  """
  returns the game score of the team tla for match_no
  returns 0 if the given team wasn't in the match
  """
  def GetTeamZone(tla, match_no):
    """
    returns the zone this team occupied during the given match,
    -1 if the team wasn't in the match
    """
    match = scheduler.match_from_ms(r.lindex("org.srobo.matches", match_no))
    
    if match == None:
      print "[sched_finals.GetGameScore.GetTeamZone] Match {0} does not exist - critical error".format(match_no)
      sys.exit(5)
      
    for i in range(len(match["teams"])):
      if match["teams"][i] == tla:
        #print tla + " were in zone {0} for match {1}".format(i, match_no)        
        return i          
     
   # print tla + " didn't play in match {0}".format(match_no)
    return -1
                  
  #print "Getting " + tla + "'s score from match {0}...".format(match_no)
  team_zone = GetTeamZone(tla, match_no)
        
  if team_zone == -1:
    #print tla + " didn't play in this match, so scored 0 in this match"
    return 0
  else:    
    temp = r.hget("org.srobo.scores.match.{0}.{1}".format(match_no, team_zone), "game_points")
    
    if temp == None:
      print "failed to get score for match {0}, zone {1}".format(match_no, team_zone)
      sys.exit(5)
    else:
      temp = int(temp)
    
    #print tla + " scored {0} points in this match".format(temp)
    return temp
      
def GetLeagueScore(tla):
  """
  returns the league score of team tla
  """
  temp = r.get("org.srobo.scores.team." + tla) 
 # print tla + " have gained {0} total league points so far".format(temp)
  return float(temp)
  
def GetTotalGameScore(tla):   
  """ 
  returns the total game score of team tla
  """
  #print "Getting total game score for team " + tla
  len_matches = r.llen("org.srobo.matches")
  
  total = 0
  for i in range(len_matches):
    temp = GetGameScore(tla, i)
        
    #print tla + " scored {0} in match {1}".format(temp, i)
    
    total = total + temp
  
  #print tla + " have score {0} total game points thus far".format(total)  
  return int(total)

def ResolveDraws(tla_list, teams_wanted, match_no = -1):
  """
  This function resolves draws in the given tla_list, returning a list of TLAs of length teams_wanted.
  In the event that any tie breaking must occur, diagnostics will be outputted.
  match_nos must be either -1 (in which case we assume we're starting on league points) and 
  teams_wanted is unrestricted, OR
  an index in org.sobo.matches, in which case teams_wanted must be <=2  
  """
  def CreateTuple(score, tla):
    return (score, tla)
    
  def GetScoreOfTuple(a_tuple):
      return a_tuple[0]
      
  # this will contain (integer score, tla) tuples.
  # can be used with remove teams to reduce the number of teams we're still considering.
  tuple_list = []
  
  # test we haven't gone insane:
  if len(tla_list) < teams_wanted:
    print "[schedule_finals] Trying to get {0} teams from {1} candidates!! ERROR".format(teams_wanted, len(tla_list))
    sys.exit(6)
  elif len(tla_list) == teams_wanted:
    # trivial case
    return tla_list
  
  def RemoveTeams(tuple_list, teams_wanted, stage_name):
    """
    removes as many teams as possible from the given list, returns the new list (still of tuples)
    will ensure a minimum of teams_wanted entries are in the list
    """
    tuple_list.sort(key = GetScoreOfTuple) # lowest scores first
    dropped_teams = []
    progressing_teams = []
    run_length = 0
    
    # trim from both ends: work out who has certainly won, and who has certainly lost.
    # work out certain winners
    #print "Determining certain winners..."
    
    while len(progressing_teams) < teams_wanted:
      if GetScoreOfTuple(tuple_list[-1]) > GetScoreOfTuple(tuple_list[-2]) and (run_length == 0):
        # then the highest item progresses
        #print tuple_list[-1][1] + " have progressed to the next stage!"  
        
        progressing_teams.append(tuple_list[-1])
        del tuple_list[-1]      
      elif run_length >= len(tuple_list) - 1:
        # run covers all of the remaining tuples, break
        break
      elif GetScoreOfTuple(tuple_list[-(run_length + 1)]) == GetScoreOfTuple(tuple_list[-(run_length + 2)]):
        # see how many items have drawn
        run_length += 1
      elif run_length > 0:
        # progress run_length items if they'll fit, else we need another stage.
        if len(progressing_teams) + run_length <= teams_wanted:
          # progress run_length items
          #print "progressing {0} teams in one go".format(run_length)
          for i in range(run_length):
            progressing_teams.append(tuple_list[-1])
            #print tuple_list[-1][1] + " have progressed to the next stage!"  
            del tuple_list[-1]
            
          run_length = 0
          continue             
        else:
          # need another stage, break
          break       
      else:
        break      
              
    run_length = 0
    
    #print "Determining certain losers..."
    
    if len(tuple_list) == 1:
      # then we have to check it against the worst progressing_team
      progressing_teams.sort(key = GetScoreOfTuple) # worst team first.
      if GetScoreOfTuple(tuple_list[0]) < GetScoreOfTuple(progressing_teams[0]):
        # they get dropped (worse than the worst team that already went through)
        #print "Dropping " + tuple_list[0][1] + " at stage " + stage_name + " because they scored too few points"
        dropped_teams.append(tuple_list[0])
        del tuple_list[0]
      
           
    while len(tuple_list) + len(progressing_teams) > teams_wanted and (len(tuple_list) > 1):
      # drop certain losers:      
      if GetScoreOfTuple(tuple_list[0]) < GetScoreOfTuple(tuple_list[1]) and (run_length == 0):
        # item 0 has certainly lost, drop it
        #print "Dropping " + tuple_list[0][1] + " at stage " + stage_name + " because they scored too few points at this stage"  
        
        dropped_teams.append(tuple_list[0])
        del tuple_list[0]
      elif run_length >= len(tuple_list) - 1:
        # run covers remaining entries, break
        break
      elif GetScoreOfTuple(tuple_list[run_length]) == GetScoreOfTuple(tuple_list[run_length + 1]):
        # count this run, see if it can be dropped in its entirety
        run_length += 1
      elif run_length > 0:
        # drop the whole run
        #print "Dropping {0} teams in one go".format(run_length)
        for i in range(run_length):
          dropped_teams.append(tuple_list[i])
                    
        del tuple_list[0:run_length]
        run_length = 0
        continue
      else:  
        break
        
    for item in tuple_list:
      progressing_teams.append(item)      
      
    print "After considering " + stage_name + " there are {0} teams competing for {1} positions".format(len(progressing_teams), teams_wanted)
    
    teams = ''
    for (score, tla) in dropped_teams:
      teams = teams + tla + ', '
      
    teams = teams[0:-len(', ')]
    
    print "and we dropped the following teams due to low score: " + teams
          
    return progressing_teams
  
  def GetTLAsFromTupleList(tuple_list):
    result = []
    for (score, tla) in tuple_list:
      result.append(tla)
      
    return result    
      
  print "\nResolving draws between {0} teams for {1} positions".format(len(tla_list), teams_wanted)
  
  if match_no != -1:
    if teams_wanted > 2:
      print """[schedule-finals] More than two teams requested from a match. This is an epic internal error!!!\n
             GET STEVEN HAYWOOD and murder him with bees"""
      sys.exit(4) # FIXME
      
    # use game score from this match first
    # print "Using game score from given match first"
    
    for tla in tla_list:
      tuple_list.append(CreateTuple(GetGameScore(tla, match_no), tla))
      
    tuple_list = RemoveTeams(tuple_list, teams_wanted, "match score")
    
    if len(tuple_list) == teams_wanted:
      return GetTLAsFromTupleList(tuple_list)
  else:
    # need to setup the tuple_list correctly for league points (score will be filled in shortly)
    for tla in tla_list:
      tuple_list.append(CreateTuple(0, tla))
          
  # Now on league points
  
  print "Now using league points on the remaining {0} teams".format(len(tuple_list))
  for i in range(len(tuple_list)):
    tuple_list[i] = CreateTuple(GetLeagueScore(tuple_list[i][1]), tuple_list[i][1])
   
  tuple_list = RemoveTeams(tuple_list, teams_wanted, "league points")
  
  if len(tuple_list) == teams_wanted:
    return GetTLAsFromTupleList(tuple_list)
    
  # Now on total game points
  print "Now using total game points on the remaining {0} teams".format(len(tuple_list))
  for i in range(len(tuple_list)):
    tuple_list[i] = CreateTuple(GetTotalGameScore(tuple_list[i][1]), tuple_list[i][1])
   
  tuple_list = RemoveTeams(tuple_list, teams_wanted, "total game points")
  
  if len(tuple_list) == teams_wanted:
    return GetTLAsFromTupleList(tuple_list)
      
  # now on human intervention:
  tuple_list.reverse() # it's currently lowest first, so reverse for highest first.
   
  print """Despite my best efforts, I cannot resolve the draw(s) between the following {0} teams.\n
I need {1} teams\n For completeness' sake, I shall print out their game, league and total game scores:\n
(If all game scores are 0, then we are trying to schedule for the quarter finals)""".format(len(tuple_list), teams_wanted)
  
  big_tuples = []
  # (TLA, game points, league points, total game points)
  for (total_game_score, tla) in tuple_list:
    if match_no == -1:
      big_tuples.append((tla, "0", str(GetLeagueScore(tla)), str(GetTotalGameScore(tla))))
    else:
      big_tuples.append((tla, str(GetGameScore(tla, match_no)), str(GetLeagueScore(tla)), str(GetTotalGameScore(tla))))
  
  print "Team TLA    Game Pts    League Pts    Total Game Pts"
    
  for (TLA, game_points, league_points, total_game_points) in big_tuples:
    print TLA + '    ' + game_points + '    ' + league_points + '    ' + total_game_points
    
  print """NB: My logic is EXTREMELY LIMITED! You must carefully determine if any teams have\n
           CLEARLY BEATEN THE FIELD and enter THOSE TLAS first\n"""           
          
  teams_entered = 0
  teams_proceeding = []
  
  while teams_entered <= teams_wanted:
    if teams_entered == teams_wanted:
      print "Please confirm your team selection:"

      for tla in teams_proceeding:
        print tla
        
      decision_confirmed = False
      decision_reached = False
      
      while not decision_reached:
        print "Enter 'yes' if this selection is CORRECT, else 'no' (without quotes) if this selection is INCORRECT"
        
        input_str = raw_input()
        if input_str == 'yes':
          decision_confirmed = True
          decision_reached = True
        elif input_str == 'no':
          decision_confirmed = False
          decision_reached = True
        else:
          continue
          
      if decision_confirmed:
        return teams_proceeding
      else:
        teams_entered = 0
        teams_proceeding = []         
    else:  
      print "Please now enter the {0} TLAs of the teams you want to PROCEED".format(teams_wanted - teams_entered)
      
      input_str = raw_input()
      
      input_str = input_str.upper() # all TLAs are capitalised
      
      if len(input_str) in range(3,5):
        # try to find it in our list:
        success = False
        for (tla, a, b, c) in big_tuples:
          if tla == input_str:
            teams_proceeding.append(tla)
            print "Added " + tla + " to the teams PROCEEDING!"
            success = True
            teams_entered += 1
            break
        
        if success:
          continue         
      
      print "'" + input_str + "' is not a (valid) TLA in the list I gave you, please try again."     
  
  
def GetTopSixteenTeamsByLeaguePoints():
  """
  returns a sorted list containing the TLAs (as strings) of the
  top sixteen teams by league points, ordered with best team first
  """ 
  print "Getting the top sixteen teams by league score..."
  # strategy: get /all/ the teams, truncate to 16 using ResolveDraws, return
  team_count = len(r.keys("org.srobo.teams*tla"))
  
  TLAS = []
  for i in range(1, team_count + 1):
    # get at org.srobo.teams.str(i).tla, put it into a list of TLAS
    TLAS.append(r.get("org.srobo.teams.{0}.tla".format(i)))

  TLAS = ResolveDraws(TLAS, 16)
  TLAS.sort(key = GetLeagueScore, reverse = True) # need to order them best team first.
  # teams equal on league points will be semi-randomly placed (sort isn't stable)
  print "Done."  
  return TLAS  


def GetTopTwoTeamsFromMatch(match_no):
  """
  returns the top two teams from the given match number (index in org.srobo.matches)
  as a list of TLAS (as strings)
  """   
  #print "Getting the top two teams from match: " + str(match_no)
  matches_len = r.llen("org.srobo.matches")
  
  if match_no not in range(matches_len):
    print """[schedule-finals] FATAL ERROR - somehow GetTopTwoTeamsFromMatch was called with match_no = {0}, 
              which is out of range!""".format(match_no)
    sys.exit(1)
  
  match = scheduler.match_from_ms(r.lindex("org.srobo.matches", match_no))
    
  temp = ResolveDraws(match["teams"], 2, match_no)  
 # print "Done getting top two teams"
  return temp
  
def create_match(start_time, teams, delay):
  return dict({"time":start_time,
              "teams": teams,
              "delay": 0})
  
def GetStartTimeOfMatch(knockout_match_no):
  """
  Returns the start time in competition time for the given knockout_match_no,
  counting from zero.
  """
  first_match_start_time = None
  
  first_match_start_time = r.get("org.srobo.schedule.final.start")
       
  if first_match_start_time == None:
    print "[schedule-finals] Your event schedule contains NO event named 'final'!!!"
    sys.exit(2)    
           
  # now have the desired start realtime for the given match_no.
  # need to magic this into competition time:
  first_knockout_match_start_time = int(first_match_start_time) - int(r.get("org.srobo.time.start"))

  return first_knockout_match_start_time + (knockout_match_no * scheduler.match_length)

def ScheduleQuarterFinals():
  """
  Schedules the quarter finals (see above)
  This includes updating the nmber of scheduled knockout matches
  """
  print "[schedule-finals] Scheduling the quarter finals..."
  top_sixteen_tlas = GetTopSixteenTeamsByLeaguePoints()
  
  start_time = GetStartTimeOfMatch(0)
  
  matches = []
  
  for match_counter in range(4):
    teams = []
    for team_counter in range(4):
      # need to schedule 4 4-team matches
      teams.append(top_sixteen_tlas[match_counter + (team_counter * 4)])
      
    m = create_match(start_time, teams, 0)
    
    matches.append(m)
    
    start_time += scheduler.match_length
    
  AppendToMatches(matches)
  r.set("org.srobo.matches.knockout_matches_scheduled", 4)  
  print "[schedule_finals] Done scheduling the QUARTER FINALS."
  
  
def ScheduleSemiFinals():
  """
  Schedules the semi finals (see above)
  This includes updating the number of scheduled knockout matches
  """
  print "[schedule-finals] Scheduling the semi finals..."
  len_matches = r.llen("org.srobo.matches")
  
  # get the top two teams from (A and B) and (C and D)
  # match A..D is len_matches - 4..1 respectively.
  top_teams = []
  for i in range(4):
    temp = GetTopTwoTeamsFromMatch(len_matches - (4 - i))
    top_teams.append(temp)
  
  # top_teams contains the top two teams from matched A..D in order (list of lists)
  match_start_time = GetStartTimeOfMatch(4)
  
  matches = []
  for match_counter in range(2):
    teams = []
    for team_index in range(2):
      for top_team_index in range(2):
        teams.append(top_teams[team_index + (match_counter * 2)][top_team_index])
        
    # teams now holds the four teams selected for match indicated by match_counter    
      
    m = create_match(match_start_time, teams, 0)
    
    match_start_time += (scheduler.match_length * 60)
    
    matches.append(m)
    
  AppendToMatches(matches)
  r.set("org.srobo.matches.knockout_matches_scheduled", 6)  
  print "[schedule-finals] Done scheduling the SEMI FINALS"
  
def ScheduleFinal():
  """
  Schedules the final (see above)
  This includes updating the number of scheduled knockout matches
  """
  print "[schedule-finals] Scheduling the final!"
    
  len_matches = r.llen("org.srobo.matches")
  
  # get the top two teams from E and F
  top_teams = []
  for i in range(2):
    temp = GetTopTwoTeamsFromMatch(len_matches - (i + 1))
    top_teams.append(temp)
  
  # top teams is a 2 list of 2 lists containing the top two teams from each match
  match_start_time = GetStartTimeOfMatch(7)
  
  matches = []
  for match_counter in range(1):
    teams = []
    for team_index in range(2):
      for top_team_index in range(2):
        teams.append(top_teams[team_index][top_team_index])
        
    # teams now holds the four teams selected for match indicated by match_counter    
      
    m = create_match(match_start_time, teams, 0)
    
    match_start_time += scheduler.match_length * 60
    
    matches.append(m)
    
  AppendToMatches(matches)
  r.set("org.srobo.matches.knockout_matches_scheduled", 7)  
  print "[schedule-finals] Done scheduling the FINAL!"
  
def main():
  """
  Determines how many knockout matches have been scheduled and schedules the next set
  """
  if not r.exists("org.srobo.matches.knockout_matches_scheduled"):
    r.set("org.srobo.matches.knockout_matches_scheduled", "0")
  
  matches_scheduled = int(r.get("org.srobo.matches.knockout_matches_scheduled"))
  
  if matches_scheduled == 0:
    ScheduleQuarterFinals()
  elif matches_scheduled == 4:
    ScheduleSemiFinals()
  elif matches_scheduled == 6:
    ScheduleFinal()
  elif matches_scheduled == 7:
    print "All knockout matches have been scheduled, nothing to do."
  else:
     # WTF?
     print "[schedule-finals] Unexpected ({0}) number of knockout matches have been scheduled!".format(matches_scheduled)
  
  
if __name__ == "__main__":
  main()
