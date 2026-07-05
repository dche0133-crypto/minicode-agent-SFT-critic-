def parse_log(line):
    level, message = line.split(':', 1)
    return {'level': level, 'message': message}
