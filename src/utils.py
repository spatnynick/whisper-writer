import yaml
import os
import tempfile
from dotenv import load_dotenv

class ConfigManager:
    _instance = None

    @staticmethod
    def config_directory():
        """Return the per-user configuration directory for the current platform."""
        if os.name == 'nt':
            base_dir = os.getenv('APPDATA') or os.getenv('LOCALAPPDATA') or os.path.expanduser('~')
            return os.path.join(base_dir, 'WhisperWriter')

        base_dir = os.getenv('XDG_CONFIG_HOME')
        if not base_dir:
            base_dir = os.path.join(os.path.expanduser('~'), '.config')
        return os.path.join(base_dir, 'whisper-writer')

    @classmethod
    def config_path(cls):
        """Return the persistent user configuration path."""
        return os.path.join(cls.config_directory(), 'config.yaml')

    @classmethod
    def sync_settings_path(cls):
        """Return the local-only synchronization settings path."""
        return os.path.join(cls.config_directory(), 'sync.yaml')

    @classmethod
    def env_path(cls):
        """Return the persistent user environment file path."""
        return os.path.join(cls.config_directory(), '.env')

    @staticmethod
    def legacy_config_path():
        """Return the configuration path used by older repository-local releases."""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')

    @staticmethod
    def legacy_env_path():
        """Return the repository-local environment path used by older releases."""
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        return os.path.join(project_root, '.env')

    @classmethod
    def ensure_config_directory(cls):
        directory = cls.config_directory()
        os.makedirs(directory, exist_ok=True)
        return directory

    @classmethod
    def load_environment(cls):
        """Load user environment values, retaining compatibility with the old root .env."""
        # Explicit process environment values win over files. Load the new location first so
        # the legacy file cannot override a migrated user value.
        load_dotenv(cls.env_path(), override=False)
        load_dotenv(cls.legacy_env_path(), override=False)

    def __init__(self):
        """Initialize the ConfigManager instance."""
        self.config = None
        self.schema = None

    @classmethod
    def initialize(cls, schema_path=None):
        """Initialize the ConfigManager with the given schema path."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance.schema = cls._instance.load_config_schema(schema_path)
            cls._instance.config = cls._instance.load_default_config()
            cls.load_environment()
            cls._instance.load_user_config()

    @classmethod
    def get_schema(cls):
        """Get the configuration schema."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")
        return cls._instance.schema

    @classmethod
    def get_config_section(cls, *keys):
        """Get a specific section of the configuration."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")

        section = cls._instance.config
        for key in keys:
            if isinstance(section, dict) and key in section:
                section = section[key]
            else:
                return {}
        return section

    @classmethod
    def get_config_value(cls, *keys):
        """Get a specific configuration value using nested keys."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")

        value = cls._instance.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return None
        return value

    @classmethod
    def set_config_value(cls, value, *keys):
        """Set a specific configuration value using nested keys."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")

        config = cls._instance.config
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            elif not isinstance(config[key], dict):
                config[key] = {}
            config = config[key]
        config[keys[-1]] = value

    @staticmethod
    def load_config_schema(schema_path=None):
        """Load the configuration schema from a YAML file."""
        if schema_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            schema_path = os.path.join(base_dir, 'config_schema.yaml')

        with open(schema_path, 'r', encoding='utf-8-sig') as file:
            schema = yaml.safe_load(file)
        return schema

    def load_default_config(self):
        """Load default configuration values from the schema."""
        def extract_value(item):
            if isinstance(item, dict):
                if 'value' in item:
                    return item['value']
                else:
                    return {k: extract_value(v) for k, v in item.items()}
            return item

        config = {}
        for category, settings in self.schema.items():
            config[category] = extract_value(settings)
        return config

    def load_user_config(self, config_path=None):
        """Load user configuration and merge with default config."""
        def deep_update(source, overrides):
            for key, value in overrides.items():
                if key not in source:
                    continue
                if isinstance(source[key], dict):
                    if isinstance(value, dict):
                        deep_update(source[key], value)
                    else:
                        print(f"Ignoring invalid configuration section: {key}")
                elif not isinstance(value, (dict, list)):
                    source[key] = value

        if config_path is None:
            config_path = self.config_path()
            if not os.path.isfile(config_path):
                # Keep existing installations working until the next successful save moves
                # their configuration into the platform-specific user directory.
                config_path = self.legacy_config_path()

        if config_path and os.path.isfile(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8-sig') as file:
                    user_config = yaml.safe_load(file)
                    if isinstance(user_config, dict):
                        deep_update(self.config, user_config)
                    elif user_config is not None:
                        print("Invalid configuration: expected a mapping. Using defaults.")
            except yaml.YAMLError:
                print("Error in configuration file. Using default configuration.")

    @classmethod
    def save_config(cls, config_path=None):
        """Save the current configuration to a YAML file."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")
        if config_path is None:
            config_path = cls.config_path()
        directory = os.path.dirname(os.path.abspath(config_path))
        os.makedirs(directory, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                             delete=False) as file:
                temporary_path = file.name
                yaml.safe_dump(cls._instance.config, file, default_flow_style=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, config_path)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    @classmethod
    def reload_config(cls):
        """
        Reload the configuration from the file.
        """
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")
        cls._instance.config = cls._instance.load_default_config()
        cls.load_environment()
        cls._instance.load_user_config()

    @classmethod
    def replace_config(cls, config):
        """Replace the in-memory configuration after an external synchronized update."""
        if cls._instance is None:
            raise RuntimeError("ConfigManager not initialized")
        cls._instance.config = config

    @classmethod
    def config_file_exists(cls):
        """Check if a valid config file exists."""
        return os.path.isfile(cls.config_path()) or os.path.isfile(cls.legacy_config_path())

    @classmethod
    def console_print(cls, message):
        """Print a message to the console if enabled in the configuration."""
        if cls._instance and cls._instance.config['misc']['print_to_terminal']:
            print(message)
