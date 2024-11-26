Feature: bootstrap annotations
  Check that user-configurable bootstrap annotations are set and removed with state change

Scenario: check annotation for cluster bootstrap protection 
    When I start postgres-0
    Then postgres-0 is a leader after 10 seconds
    When I start postgres-1 in a cluster batman1 as a long-running clone of postgres-0
    Then "members/postgres-1" key in DCS has state=running custom bootstrap script after 20 seconds
    And postgres-1 is annotated with "foo"
    And postgres-1 is a leader of batman1 after 20 seconds

Scenario: check annotation for replica bootstrap protection
    When I do a backup of postgres-1
    And I start postgres-2 in cluster batman1 using long-running backup_restore
    Then "members/postgres-2" key in DCS has state=creating replica after 20 seconds
    And postgres-2 is annotated with "foo"

Scenario: check annotation is removed
    Given "members/postgres-1" key in DCS has state=running after 2 seconds
    And  "members/postgres-2" key in DCS has state=running after 20 seconds
    Then postgres-1 is not annotated with "foo"
    And postgres-2 is not annotated with "foo"
