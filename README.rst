****************
cinq-auditor-ebs
****************

===========
Description
===========

This auditor validates that EBS volumes are tagged and can be configured to take corrective action, if required.

Please check out the `README <https://github.com/RiotGames/cloud-inquisitor/blob/master/docs/backend/README.rst>`_ 
for further details on the how ``cinq-auditor-ebs`` works with further details on ``Cloud Inquisitor`` backend is built and what technologies we use.

=====================
Configuration Options
=====================

+------------------------+-------------------------+-----------------------------------------------------------------------------------------------------+
| Option name            | Default Value           | Type   | Description                                                                                |
+========================+=========================+=====================================================================================================+
| enabled                | False                   | bool   | Enable the EBS auditor                                                                     |
+------------------------+-------------------------+-----------------------------------------------------------------------------------------------------+
| interval               | 1440                    | int    | How often the auditor runs, in minutes                                                     |
+------------------------+-------------------------+-----------------------------------------------------------------------------------------------------+
| renotify_delay_days    | 14                      | int    | Send another notifications n days after the last                                           |
+------------------------+-------------------------+-----------------------------------------------------------------------------------------------------+
| email_subject          | Unattached EBS Volumes  | string | JSON document with roles to push to accounts. See documentation for examples               |
+------------------------+-------------------------+-----------------------------------------------------------------------------------------------------+
| ignore_tags            | cinq_ignore             | array | A list of tags that will cause the auditor to ignore the volume                             |
+------------------------+-------------------------+-----------------------------------------------------------------------------------------------------+
