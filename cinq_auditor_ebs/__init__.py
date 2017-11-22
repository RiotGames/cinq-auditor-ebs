import urllib.parse
import uuid
from datetime import datetime

from cloud_inquisitor import db
from cloud_inquisitor.config import dbconfig, ConfigOption
from cloud_inquisitor.constants import NS_AUDITOR_EBS, EBSIssueState, NS_EMAIL
from cloud_inquisitor.exceptions import SlackError
from cloud_inquisitor.plugins import BaseAuditor
from cloud_inquisitor.plugins.notifiers.email import send_email
from cloud_inquisitor.plugins.notifiers.slack import SlackNotifier
from cloud_inquisitor.plugins.types.issues import EBSVolumeAuditIssue
from cloud_inquisitor.plugins.types.resources import EBSVolume
from cloud_inquisitor.utils import get_template, get_resource_id


class EBSAuditor(BaseAuditor):
    """Known issue: if this runs before collector, we don't have EBSVolume or EBSVolumeAttachment data."""
    name = 'EBS Auditor'
    ns = NS_AUDITOR_EBS
    interval = dbconfig.get('interval', ns, 1440)
    enabled = dbconfig.get('enabled', ns, False)
    options = (
        ConfigOption('enabled', False, 'bool', 'Enable the EBS auditor'),
        ConfigOption('interval', 1440, 'int', 'How often the auditor runs, in minutes'),
        ConfigOption('renotify_delay_days', 14, 'int', 'Send another notifications n days after the last'),
        ConfigOption('email_subject', 'Unattached EBS Volumes', 'string', 'Subject of the notification emails'),
        ConfigOption('ignore_tags', ['cinq:ignore'], 'array',
                     'A list of tags that will cause the auditor to ignore the volume')
    )

    def __init__(self):
        super().__init__()
        self.subject = self.dbconfig.get('email_subject', self.ns)

    def run(self, *args, **kwargs):
        """Main execution point for the auditor

        Args:
            *args:
            **kwargs:

        Returns:
            `None`
        """
        self.log.debug('Starting EBSAuditor')
        data = self.update_data()

        notices = {}
        for account, issues in data.items():
            account.contacts.append('#akjaer-slack-test')

            for issue in issues['issues']:
                for recipient in account.contacts:
                    notices.setdefault(recipient, {'issues': [], 'fixed': []})['issues'].append(issue)

            for issue in issues['fixed']:
                for recipient in account.contacts:
                    notices.setdefault(recipient, {'issues': [], 'fixed': []})['fixed'].append(issue)

        self.notify(notices)

    def update_data(self):
        """Update the database with the current state and return a dict containing the new / updated and fixed
        issues respectively, keyed by the account object

        Returns:
            `dict`
        """
        existing_issues = EBSVolumeAuditIssue.get_all()

        volumes = self.get_unattached_volumes()
        new_issues = self.process_new_issues(volumes, existing_issues)
        fixed_issues = self.process_fixed_issues(volumes, existing_issues)

        # region Process the data to be returned
        output = {}
        for acct, data in list(new_issues.items()):
            output.setdefault(acct, {'issues': [], 'fixed': []})['issues'] += data

        for acct, data in list(fixed_issues.items()):
            output.setdefault(acct, {'issues': [], 'fixed': []})['fixed'] += data
        # endregion

        # region Update the database with the changes pending
        for issues in new_issues.values():
            for issue in issues:
                db.session.add(issue.issue)

        for issues in fixed_issues.values():
            for issue in issues:
                db.session.delete(issue.issue)

        db.session.commit()
        # endregion

        return output

    def get_unattached_volumes(self):
        """Build a list of all volumes missing tags and not ignored. Returns a `dict` keyed by the issue_id with the
        volume as the value

        Returns:
            :obj:`dict` of `str`: `EBSVolume`
        """
        volumes = {}
        ignored_tags = dbconfig.get('ignore_tags', self.ns)
        for volume in EBSVolume.get_all().values():
            issue_id = get_resource_id('evai', volume.id)

            if len(volume.attachments) == 0:
                if len(list(filter(set(ignored_tags).__contains__, [tag.key for tag in volume.tags]))):
                    continue

                volumes[issue_id] = volume

        return volumes

    def process_new_issues(self, volumes, existing_issues):
        """Takes a dict of existing volumes missing tags and a dict of existing issues, and finds any new or updated
        issues.

        Args:
            volumes (:obj:`dict` of `str`: `EBSVolume`): Dict of current volumes with issues
            existing_issues (:obj:`dict` of `str`: `EBSVolumeAuditIssue`): Current list of issues

        Returns:
            :obj:`dict` of `str`: `EBSVolumeAuditIssue`
        """
        new_issues = {}
        for issue_id, volume in volumes.items():
            state = EBSIssueState.DETECTED.value

            if issue_id in existing_issues:
                issue = existing_issues[issue_id]

                data = {
                    'state': state,
                    'notes': issue.notes,
                    'last_notice': issue.last_notice
                }
                if issue.update(data):
                    new_issues.setdefault(issue.volume.account, []).append(issue)
                    self.log.debug('Updated EBSVolumeAuditIssue {}'.format(
                        issue_id
                    ))

            else:
                properties = {
                    'volume_id': volume.id,
                    'account_id': volume.account_id,
                    'location': volume.location,
                    'state': state,
                    'last_change': datetime.now(),
                    'last_notice': None,
                    'notes': []
                }

                issue = EBSVolumeAuditIssue.create(issue_id, properties=properties)
                new_issues.setdefault(issue.volume.account, []).append(issue)

        return new_issues

    def process_fixed_issues(self, volumes, existing_issues):
        """Provided a list of volumes and existing issues, returns a list of fixed issues to be deleted

        Args:
            volumes (`dict`): A dictionary keyed on the issue id, with the :obj:`Volume` object as the value
            existing_issues (`dict`): A dictionary keyed on the issue id, with the :obj:`EBSVolumeAuditIssue` object as
            the value

        Returns:
            :obj:`dict` of `str`: `EBSVolumeAuditIssue`
        """
        fixed_issues = {}
        for issue_id, issue in list(existing_issues.items()):
            if issue_id not in volumes:
                fixed_issues.setdefault(issue.volume.account, []).append(issue)

        return fixed_issues

    def notify(self, notices):
        """Send notifications to the users via. the provided methods

        Args:
            notices (:obj:`dict` of `str`: `dict`): List of the notifications to send

        Returns:
            `None`
        """
        for recipient, data in list(notices.items()):
            if recipient.startswith('#'):
                self.notify_slack(recipient, data)

            elif recipient.find('@') >= 0:
                self.notify_email(recipient, data)

    def notify_email(self, recipient, data):
        """Notify a user via email

        Args:
            recipient (`str`): Email address
            data (`dict`): List of issues

        Returns:
            `None`
        """
        if len(data['issues']) > 0:
            tmpl = get_template('unattached_ebs_volume.html')
            message_uuid = urllib.parse.quote(str(uuid.uuid4()))
            body = tmpl.render(
                issues=data['issues']
            )

            send_email(
                self.name,
                self.dbconfig.get('from_address', NS_EMAIL),
                recipient,
                self.subject,
                html_body=body,
                message_uuid=message_uuid
            )

        # TODO: Currently disabled as we do not enforce deletion. When we do implement deletion uncomment this code
        # TODO: and provide the template requested
        # if len(data['fixed']) > 0:
        #     tmpl = get_template('unattached_ebs_volume_fixed.html')
        #
        #     message_uuid = urllib.parse.quote(str(uuid.uuid4()))
        #     body = tmpl.render(
        #         fixed=data['fixed']
        #     )
        #
        #     send_email(
        #         self.name,
        #         self.dbconfig.get('from_address', NS_EMAIL),
        #         recipient,
        #         self.subject,
        #         html_body=body,
        #         message_uuid=message_uuid
        #     )

    def notify_slack(self, recipient, data):
        """Notify a slack channel

                Args:
                    recipient (`str`): Slack channel name
                    data (`dict`): List of issues

                Returns:
                    `None`
                """
        try:
            if len(data['issues']) > 0:
                text_tmpl = get_template('unattached_ebs_volume.txt')
                message = text_tmpl.render(
                    issues=data['issues']
                )

                SlackNotifier.send_message(
                    recipient,
                    message
                )

            # TODO: Currently disabled as we do not enforce deletion. When we do implement deletion uncomment this code
            # TODO: and provide the template requested
            # if len(data['fixed']) > 0:
            #     text_tmpl = get_template('required_tags_fixed.txt')
            #     message = text_tmpl.render(
            #         issues=data['fixed']
            #     )
            #
            #     SlackNotifier.send_slack_message(
            #         (recipient,),
            #         message
            #     )
        except SlackError as ex:
            self.log.error('Failed sending message to slack channel {}: {}'.format(recipient, ex))
