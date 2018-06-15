# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

import io
import logging
import os
import platform
import sys
import tempfile
import uuid
import zipfile

import humanfriendly
import requests
from knack.util import CLIError
from vsts.cli.common.services import get_vss_connection

logger = logging.getLogger('vsts.packaging')

class ArtifactToolUpdater:
    ARTIFACTTOOL_OVERRIDE_PATH_ENVKEY = "VSTS_CLI_ARTIFACTTOOL_OVERRIDE_PATH"
    ARTIFACTTOOL_OVERRIDE_URL_ENVKEY = "VSTS_CLI_ARTIFACTTOOL_OVERRIDE_URL"
    ARTIFACTTOOL_OVERRIDE_VERSION_ENVKEY = "VSTS_CLI_ARTIFACTTOOL_OVERRIDE_VERSION"

    def get_latest_artifacttool(self, team_instance):
        artifacttool_binary_override_path = os.environ.get(self.ARTIFACTTOOL_OVERRIDE_PATH_ENVKEY)
        if artifacttool_binary_override_path is not None:
            artifacttool_binary_path = artifacttool_binary_override_path
            logger.debug("ArtifactTool path was overriden to '%s' due to environment variable %s" % (artifacttool_binary_path, self.ARTIFACTTOOL_OVERRIDE_PATH_ENVKEY))
        else:
            logger.debug("Checking for a new ArtifactTool")
            artifacttool_binary_path = self._get_artifacttool(team_instance)
        return artifacttool_binary_path

    def _get_artifacttool(self, team_instance):
        logger.debug("Checking for ArtifactTool updates")

        # Call the auto-update API to find the current version of ArtifactTool
        # If VSTS_ARTIFACTTOOL_OVERRIDE_URL is set, instead always download from the URL
        artifacttool_override_url = os.environ.get(self.ARTIFACTTOOL_OVERRIDE_URL_ENVKEY)
        if artifacttool_override_url is not None:
            release_uri = artifacttool_override_url
            release_id = "custom_%s" % str(uuid.uuid4()) # ensures that the custom URL is always downloaded fresh
        else:
            override_version = os.environ.get(self.ARTIFACTTOOL_OVERRIDE_VERSION_ENVKEY)
            try:
                release = self._get_current_release(team_instance, override_version)
            except Exception as ex:
                raise CLIError('Failed to find UPack tooling: %s' % ex) 
            release_uri, release_id = release

        # Determine the path for the release, and skip downloading if it already exists
        logger.debug("Checking if we already have ArtifactTool release '%s'", release_id)
        release_dir = self._get_release_dir(release_id)
        if os.path.exists(release_dir):
            logger.debug("Not updating ArtifactTool because the current release already exists at '%s'" % release_dir)
            return release_dir
              
        # Doesn't already exist. Download and extract the release.
        logger.debug("Updating to ArtifactTool release %s since it doesn't exist at %s" % (release_id, release_dir))
        self._update_artifacttool(release_uri, release_dir, release_id)

        return release_dir

    def _get_current_release(self, team_instance, override_version):
        connection = get_vss_connection(team_instance)
        client = connection.get_client('vsts.cli.package.common.client_tool.client_tool_client.ClientToolClient')
        logger.debug("Looking up current version of ArtifactTool...")
        release = client.get_clienttool_release("ArtifactTool", os_name=platform.system(), arch=platform.machine(), version=override_version)
        return (release.uri, self._compute_id(release)) if release is not None else None

    def _update_artifacttool(self, uri, release_dir, release_id):
        with humanfriendly.Spinner(label="Downloading UPack tooling (%s)" % release_id, total=100, stream=sys.stderr) as spinner:
            spinner.step()
            logger.debug("Downloading ArtifactTool from %s" % uri)

            # Make the request, determine the total size
            response = requests.get(uri, stream=True)
            content_length_header = response.headers['Content-Length'].strip()
            content_length = int(content_length_header)

            # Do the download, updating the progress bar
            content=io.BytesIO()
            bytes_so_far = 0
            for chunk in response.iter_content(chunk_size=1024*512):
                if chunk:
                    content.write(chunk)
                    bytes_so_far += len(chunk)
                    spinner.step(100 * float(bytes_so_far)/float(content_length))

            # Extract the zip
            release_temp_dir = self._get_temp_release_dir()
            logger.debug("Extracting ArtifactTool to %s" % release_temp_dir)
            f = zipfile.ZipFile(content)
            f.extractall(path=release_temp_dir)

            # Move the directory into the real location
            logger.debug("Moving downloaded ArtifactTool from %s to %s" % (release_temp_dir, release_dir))
            os.rename(release_temp_dir, release_dir)
            logger.info("Downloaded UPack tooling successfully")

    def _mkdir_if_not_exist(self, path):
        try: 
            os.makedirs(path)
        except OSError:
            # Ignore errors that were likely because the directory already exists
            if not os.path.isdir(path):
                raise

    def _compute_id(self, release):
        return "%s_%s_%s" % (release.name, release.rid, release.version)

    def _compute_artifact_root(self):
        temp_dir = tempfile.gettempdir()
        return os.path.join(temp_dir, "ArtifactTool")

    def _get_release_dir(self, release_id):
        artifact_root = self._compute_artifact_root()
        releases_root = os.path.join(artifact_root, "releases")
        self._mkdir_if_not_exist(releases_root)
        return os.path.join(releases_root, release_id)

    def _get_temp_release_dir(self):
        artifact_root = self._compute_artifact_root()
        releases_temp_root = os.path.join(artifact_root, "temp")
        self._mkdir_if_not_exist(releases_temp_root)
        return os.path.join(releases_temp_root, str(uuid.uuid4()))
