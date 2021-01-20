#! /usr/bin/env python

import codecs
import copy
import hashlib
import os
import sys

import yaml
from datetime import datetime, tzinfo, timedelta


CRD_KIND = 'CustomResourceDefinition'
CROSSPLANE_CRD_SUBDIR = 'cluster/charts/crossplane-types/crds/'
CROSSPLANE_README_PATH = 'docs/README.md'


class literal_unicode(unicode): pass
def literal_unicode_representer(dumper, data):
    return dumper.represent_scalar(u'tag:yaml.org,2002:str', data, style='|')
yaml.add_representer(literal_unicode, literal_unicode_representer)

from yaml.representer import SafeRepresenter
def change_style(style, representer):
    def new_representer(dumper, data):
        scalar = representer(dumper, data)
        scalar.style = style
        return scalar
    return new_representer
class literal_str(str): pass
represent_literal_str = change_style('|', SafeRepresenter.represent_str)
yaml.add_representer(literal_str, represent_literal_str)


class OHPackageUpgrader(object):
	path = None
	def __init__(self, basepath, csv):
		self.basepath = basepath
		self.path = os.path.join(basepath, 'crossplane.package.yaml')
		self.csv = csv

	def write_upgraded(self):
		channel_obj = dict(currentCSV=self.csv.name(), name="alpha")
		doc = None
		with open(self.path) as fh:
			doc = yaml.safe_load(fh)
			doc['channels'] = [channel_obj]
		with open(self.path, 'w+') as fh:
			yaml.dump(doc, fh)


class CRD(object):
	path = None
	doc = None
	contents = None

	def __init__(self, path):
		self.path = path
		with open(path) as fh:
			self.contents = fh.read()
			self.doc = yaml.safe_load(self.contents)

	def name(self):
		return self.doc['metadata']['name']

	def group(self):
		return self.doc['spec']['group']

	def version(self):
		return self.doc['spec']['version']

	def kind(self):
		return self.doc['spec']['names']['kind']

	def description(self):
		d = self.doc["spec"]["validation"]["openAPIV3Schema"].get("description")
		if not d:
			return self.name()
		return d

	def is_crd(self):
		return self.doc['kind'] == CRD_KIND

	def digest(self):
		return hashlib.sha1(self.contents).hexdigest()

	def nice_filename(self):
		return "{}.yaml".format(self.name())

	def write_to_basepath(self, basepath):
		filename = self.nice_filename()
		path = os.path.join(basepath, filename)
		with open(path, 'w+') as fh:
			yaml.dump(self.doc, fh)


class Directory(object):
	path = None
	_yaml_files = None
	_crds = None

	def __init__(self, path):
		self.path = path
		self._index_crds()

	def _yaml_files(self):
		for root, dirs, files in os.walk(self.path):
			for f in files:
				if f.endswith('.yaml'):
					yield os.path.join(root, f)

	def _index_crds(self):
		self._crds = {}
		for path in self._yaml_files():
			crd = CRD(path)
			if crd.is_crd():
				self._crds[crd.name()] = crd

	def crds(self):
		return self._crds.values()

	def get_by_name(self, name):
		return self._crds.get(name)

	def crd_names(self):
		return set(self._crds.keys())

	def names_missing(self, other_directory):
		return self.crd_names() - other_directory.crd_names()

	def names_intersecting(self, other_directory):
		return self.crd_names() & other_directory.crd_names()


class Readme(object):
	path = None
	def __init__(self, basepath):
		self.path = os.path.join(basepath, CROSSPLANE_README_PATH)

	def get_contents(self):
		with open(self.path) as fh:
			return fh.read()

	def get_contents_as_literal_unicode(self):
		return read_file_as_literal_unicode(self.path)

	def first_paragraph(self):
		buffer = ''
		with open(self.path) as fh:
			for line in fh:
				if line.strip() == '':
					return buffer
				buffer += line


class ClusterServiceVersion(object):
	def __init__(self, version, crd_dir, readme=None, prev_csv=None):
		if version.startswith("v"):
			raise Exception("version should not start with 'v'")
		self.version = version
		self.readme = readme
		self.crd_dir = crd_dir
		self.prev_csv = prev_csv

	def v_version(self):
		return "v{}".format(self.version)

	def image(self):
		return "crossplane/crossplane:{}".format(self.v_version())

	def name(self):
		return "crossplane.{}".format(self.v_version())

	def path(self):
		filename = "{}.clusterserviceversion.yaml".format(self.name())
		return os.path.join(self.crd_dir.path, filename)

	def read_yaml(self):
		with open(self.path()) as fh:
			return yaml.safe_load(fh)

	def write_csv(self):
		print self.path()
		with open(self.path(), 'w+') as fh:
			yaml.dump(self.render(), fh)

	def render(self):
		assert self.prev_csv
		prev = self.prev_csv.read_yaml()
		self.update_metadata_name(prev)
		self.update_container_image(prev)
		self.update_created_at(prev)
		self.update_description(prev)
		self.update_description_annotation(prev)
		self.update_version(prev)
		self.update_crds(prev)
		self.update_deployments(prev)
		self.update_keywords(prev)
		self.update_links(prev)
		self.update_provided_by(prev)
		self.update_cluster_permissions(prev)
		self.update_replaces(prev)
		return prev

	def render_yaml(self):
		return yaml.dump(self.render())

	def update_metadata_name(self, doc):
		doc["metadata"]["name"] = self.name()

	def update_container_image(self, doc):
		doc["metadata"]['annotations']["containerImage"] = self.image()

	def update_created_at(self, doc):
		doc["metadata"]['annotations']["createdAt"] = now_8601()

	def update_description_annotation(self, doc):
		doc["metadata"]["annotations"]["description"] = "Manage any infrastructure your applications need directly from Kubernetes."

	def update_description(self, doc):
		#if self.readme is not None:
			#doc["spec"]["description"] = self.readme.get_contents_as_literal_unicode()
		doc["spec"]["description"] = literal_str(full_description())

	def update_version(self, doc):
		doc["spec"]["version"] = self.version

	def update_crds(self, doc):
		owned = []
		for crd in self.crd_dir.crds():
			owned.append(
				{
					"description": crd.description(),
					"displayName": crd.kind(),
					"kind": crd.kind(),
					"name": crd.name(),
					"version": crd.version(),
				}
			)
		doc["spec"]["customresourcedefinitions"]["owned"] = owned


	def update_cluster_permissions(self, doc):
		api_groups = set()
		for crd in self.crd_dir.crds():
			api_groups.add(crd.group())

		# api_groups = ['cache.crossplane.io', 'compute.crossplane.io', 'database.crossplane.io', 'kubernetes.crossplane.io', 'core.crossplane.io', 'stacks.crossplane.io', 'storage.crossplane.io', 'workload.crossplane.io', 'core.oam.dev'],

		doc['spec']['install']['spec']['clusterPermissions'] = [{
			'rules': [
				{
					'apiGroups': [''],
					'verbs': ['create', 'update', 'patch', 'delete'],
					'resources': ['events']
				},
				{
					'apiGroups': [''],
					'verbs': ['get', 'list', 'watch', 'create', 'update'],
					'resources': ['secrets']
				},
				{
					'apiGroups': ['apiextensions.k8s.io'],
					'verbs': ['get', 'list', 'watch', 'create', 'update'],
					'resources': ['customresourcedefinitions'],
				},
				{
					'apiGroups': sorted(list(api_groups)),
					'verbs': ['*'],
					'resources': ['*']
				},
			],
			'serviceAccountName': 'crossplane'
		},
		{
			'rules': [
				{
					'apiGroups': ['*'],
					'verbs': ['*'], 'resources': ['*']
				}],
			'serviceAccountName': 'crossplane-package-manager'
		}]
		

	def update_provided_by(self, doc):
		doc["spec"]["provider"] = dict(name="Upbound")

	def update_deployments(self, doc):
		deployments = doc["spec"]["install"]["spec"]["deployments"]
		for d in deployments:
			if d["name"] == "crossplane":
				d["spec"]["template"]["spec"]["containers"][0]["image"] = self.image()
				continue
			if d["name"] == "crossplane-package-manager":
				d["spec"]["template"]["spec"]["containers"][0]["image"] = self.image()
				for e in d["spec"]["template"]["spec"]["containers"][0]["env"]:
					if e["name"] == "PACKAGE_MANAGER_IMAGE":
						e["value"] = self.image()
				continue
			raise Exception("Unrecognized deployment!")

	def update_keywords(self, doc):
		doc["spec"]["keywords"] = [
			"cloud", "infrastructure", "services", "application", "database",
			"cache", "bucket", "infra", "app", "ops", "oam", "gcp", "azure", 
			"aws", "alibaba", "cloudsql", "rds", "s3", "azuredatabase", 
			"asparadb", "gke", "aks", "eks"]

	def update_links(self, doc):
		doc["spec"]["links"] = [
			dict(name="GitHub", url="https://github.com/crossplane/crossplane"),
			dict(name="Website", url="https://crossplane.io"),
			dict(name="Twitter", url="https://twitter.com/crossplane_io"),
			dict(name="Slack", url="https://slack.crossplane.io/"),
		]

	def update_replaces(self, doc):
		doc["spec"]["replaces"] = self.prev_csv.name()

def main(crossplane_path, ophub_path, cur_ver, new_ver):
	oh_cur_path = os.path.join(ophub_path, cur_ver)
	oh_new_path = os.path.join(ophub_path, new_ver)
	xp_crd_path = os.path.join(crossplane_path, CROSSPLANE_CRD_SUBDIR)
	xp_dir = Directory(xp_crd_path)
	oh_cur_dir = Directory(oh_cur_path)

	print_change_report(xp_dir, oh_cur_dir)

	try:
		os.mkdir(oh_new_path)
	except:
		pass

	for crd in xp_dir.crds():
		crd.write_to_basepath(oh_new_path)

	oh_new_dir = Directory(oh_new_path)
	readme = Readme(crossplane_path)
	prev_csv = ClusterServiceVersion(cur_ver, oh_cur_dir)
	csv = ClusterServiceVersion(new_ver, oh_new_dir, readme=readme, prev_csv=prev_csv)
	print csv.render_yaml()
	csv.write_csv()

	op = OHPackageUpgrader(ophub_path, csv)	
	op.write_upgraded()

def print_change_report(xp_dir, oh_cur_dir):
	print "+: added, -: removed, %: changed"
	table_fmt = "{} {:<55} {:<60} {:<50}"
	for added in xp_dir.names_missing(oh_cur_dir):
		print table_fmt.format("+", added, os.path.basename(xp_dir.get_by_name(added).path), "")

	for removed in oh_cur_dir.names_missing(xp_dir):
		print table_fmt.format("-", removed, os.path.basename(oh_cur_dir.get_by_name(removed).path), "")

	for maybe_modified in xp_dir.names_intersecting(oh_cur_dir):
		xp_crd = xp_dir.get_by_name(maybe_modified)
		oh_crd = oh_cur_dir.get_by_name(maybe_modified)
		if xp_crd.digest() != oh_crd.digest():
			print table_fmt.format("%", maybe_modified, os.path.basename(xp_crd.path), os.path.basename(oh_crd.path))

def read_file_as_literal_unicode(path):
	#with codecs.open(path, encoding='utf-8') as fh:
	with open(path) as fh:
		contents = fh.read()
		return literal_str(contents)

def now_8601():
	class simple_utc(tzinfo):
		def tzname(self,**kwargs):
			return "UTC"
		def utcoffset(self, dt):
			return timedelta(0)
	return datetime.utcnow().replace(tzinfo=simple_utc()).isoformat()
	#return n.replace('+00:00', 'Z')


def full_description(): 
	return u'''# Overview

![Crossplane](media/banner.png)

Crossplane is an open source Kubernetes add-on that extends any cluster with
the ability to provision and manage cloud infrastructure, services, and
applications using kubectl, GitOps, or any tool that works with the Kubernetes
API.

With Crossplane you can:

  * **Provision & manage cloud infrastructure with kubectl**
    * [Install Crossplane] to provision and manage cloud infrastructure and
      services from any Kubernetes cluster.
    * Provision infrastructure primitives from any provider ([GCP], [AWS],
      [Azure], [Alibaba], on-prem) and use them alongside existing application
      configurations.
    * Version, manage, and deploy with your favorite tools and workflows that
      you're using with your clusters today.

  * **Publish custom infrastructure resources for your applications to use**
    * Define, compose, and publish your own [infrastructure resources] with
      declarative YAML, resulting in your own infrastructure CRDs being added to
      the Kubernetes API for applications to use.
    * Hide infrastructure complexity and include policy guardrails, so
      applications can easily and safely consume the infrastructure they need,
      using any tool that works with the Kubernetes API.
    * Consume infrastructure resources alongside any Kubernetes application to
      provision and manage the cloud services they need with Crossplane as an
      add-on to any Kubernetes cluster.

  * **Deploy applications using a team-centric approach with OAM**
    * Define cloud native applications and the infrastructure they require with
      the Open Application Model ([OAM]).
    * Collaborate with a team-centric approach with a strong separation of
      concerns:
        * Infrastructure operators - provide infrastructure and services for
          applications to consume
        * Application developers - build application components independent of
          infrastructure
        * Application operators - compose, deploy, and run application
          configurations
    * Deploy application configurations from app delivery pipelines or GitOps
      workflows, using the proven Kubernetes declarative model.

## Getting Started
[Install Crossplane] into any Kubernetes cluster to get started.

## Mission

Crossplane strives to be the best Kubernetes add-on to provision and manage the
infrastructure and services your applications need directly from kubectl. A
huge part of this mission is arriving at an elegant, flexible way to define,
compose, and publish your own infrastructure resources to the Kubernetes API
and to model and manage cloud native applications.

The path of cloud native apps from developer laptop into production requires
collaboration across teams to build the app itself, deploy and manage the app
and it's infrastructure, and publishing infrastructure resources that embody
organizational best practices and security policies.

Today, multiple tools and management models must be glued together in
deployment pipelines that are often fragile and error prone. Teams can find it
difficult to collaborate in an effective way when aspects of an application are
blurred, resulting in a lack of clear ownership and conflicts integrating
changes. Requiring team members to master multiple tools, languages, and
philosophies, while understanding the interactions and failure modes between
them can significantly impede an organization's ability to deliver applications
efficiently.

Crossplane believes that a team-centric approach with a strong separation of
concerns combined with the proven Kubernetes declarative model is the best way
to provision and manage infrastructure and cloud native applications. Teams
should be able to publish infrastructure resources for applications to consume,
define application components independent of infrastructure, and compose both
into complete application configurations -- all using declarative YAML that can
be deployed with kubectl from app delivery pipelines or with GitOps workflows.

This team-centric approach reflects individuals often specializing in the
following roles:

    *   **Infrastructure Operators** - provide infrastructure and services for apps
        to consume
    *   **Application Developers** - build application components independent of
        infrastructure
    *   **Application Operators** - compose, deploy, and run application
        configurations

This separation of concerns is core to Crossplane's approach to infrastructure
and application management, so team members can deliver value by focusing on
what they know best.

With Crossplane, infrastructure operators can define custom infrastructure
resources with declarative YAML and publish them for applications to consume
as Kubernetes custom resources or with any tool that works with the Kubernetes
API. These infrastructure resources can be used with existing Kubernetes
applications (Deployments, Services) and with application definition models
like OAM.

The result is a consistent, integrated, and modular approach to managing
infrastructure and application configurations, that can be deployed with the
same tooling including kubectl, GitOps, and anything can talk with the
Kubernetes API.

<!-- Named Links -->

[Install Crossplane]: getting-started/install.md
[Custom Resource Definitions]: https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/
[reconciling]: https://kubernetes.io/docs/concepts/architecture/controller/
[GCP]: https://github.com/crossplane/provider-gcp
[AWS]: https://github.com/crossplane/provider-aws
[Azure]: https://github.com/crossplane/provider-azure
[Alibaba]: https://github.com/crossplane/provider-alibaba
[infrastructure resources]: https://blog.crossplane.io/crossplane-v0-10-compose-and-publish-your-own-infrastructure-crds-velero-backup-restore-compatibility-and-more/
[OAM]: https://oam.dev/#implementation
'''


# KUBE_VER=v1.17.5 make operator.test OP_PATH=community-operators/crossplane/ INSTALL_MODE=AllNamespaces VERBOSE=1
if __name__ == '__main__':
	crossplane_path = sys.argv[1]
	ophub_path = sys.argv[2]
	ophub_cur_ver = sys.argv[3]
	ophub_next_ver = sys.argv[4]
	main(crossplane_path, ophub_path, ophub_cur_ver, ophub_next_ver)
