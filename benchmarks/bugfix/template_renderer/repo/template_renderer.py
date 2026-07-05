def render(template, values):
    for key, value in values.items():
        template = template.replace('{{' + key + '}}', str(value))
    return template
