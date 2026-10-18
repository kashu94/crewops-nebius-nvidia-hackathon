import { AppRegistry, LogBox } from 'react-native';
import { App } from './src/App';
import { name as appName } from './app.json';

// No yellow-box warnings over the wall display.
LogBox.ignoreAllLogs();

AppRegistry.registerComponent(appName, () => App);
