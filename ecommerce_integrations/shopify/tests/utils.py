import os
import json

def load_json(filename):
	"""Load json file from shopify/tests/data directory."""
	dir = os.path.dirname(os.path.realpath(__file__))

	filepath = os.path.join(dir, "data", filename)
	with open(filepath) as json_file:
		data = json.load(json_file)

	return data
