from pygbif import species as species
import json

x = species.name_backbone(name='Aphelocoma californica')

print(json.dumps(x, indent=2))