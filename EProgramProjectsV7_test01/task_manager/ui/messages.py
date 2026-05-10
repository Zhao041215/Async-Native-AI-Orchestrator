def success(message: str) -> str:
    return f"OK: {message}"


def error(message: str) -> str:
    return f"ERROR: {message}"


def info(message: str) -> str:
    return f"INFO: {message}"


def help_text(app_name: str = "task") -> str:
    return (
        f"{app_name} - simple task manager\n\n"
        "Usage:\n"
        f"  {app_name} add <title>\n"
        f"  {app_name} list\n"
        f"  {app_name} done <id>\n"
        f"  {app_name} remove <id>\n\n"
        "Examples:\n"
        f"  {app_name} add Buy milk\n"
        f"  {app_name} done 2"
    )
