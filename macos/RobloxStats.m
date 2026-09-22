#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <Security/Security.h>

static NSURL *dataURL(void) { return [NSHomeDirectory() length] ? [NSURL fileURLWithPath:[NSHomeDirectory() stringByAppendingPathComponent:@".robloxstats"]] : nil; }
static NSMutableDictionary *keyQuery(void) { return [@{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,(__bridge id)kSecAttrService:@"RobloxStats Resend",(__bridge id)kSecAttrAccount:@"api-key"} mutableCopy]; }
@interface AppDelegate : NSObject <NSApplicationDelegate,NSWindowDelegate,WKNavigationDelegate,WKDownloadDelegate>
@property NSWindow *window;
@property WKWebView *web;
@property NSStatusItem *statusItem;
@property NSTask *tracker;
@property BOOL terminating;
@end
@implementation AppDelegate
- (NSURL *)home { return [NSURL URLWithString:@"http://127.0.0.1:8765/"]; }
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSMenu *menu=[NSMenu new], *appMenu=[NSMenu new]; NSMenuItem *root=[NSMenuItem new]; root.submenu=appMenu; [menu addItem:root];
    [appMenu addItemWithTitle:@"Show RobloxStats" action:@selector(showWindow:) keyEquivalent:@"0"];
    [appMenu addItem:NSMenuItem.separatorItem];
    [appMenu addItemWithTitle:@"Quit RobloxStats" action:@selector(terminate:) keyEquivalent:@"q"];
    NSMenu *edit=[[NSMenu alloc] initWithTitle:@"Edit"]; NSMenuItem *editRoot=[[NSMenuItem alloc] initWithTitle:@"Edit" action:nil keyEquivalent:@""]; editRoot.submenu=edit; [menu addItem:editRoot];
    NSArray *titles=@[@"Cut",@"Copy",@"Paste",@"Select All"],*actions=@[@"cut:",@"copy:",@"paste:",@"selectAll:"],*keys=@[@"x",@"c",@"v",@"a"];
    for (NSUInteger i=0;i<titles.count;i++) [edit addItemWithTitle:titles[i] action:NSSelectorFromString(actions[i]) keyEquivalent:keys[i]];
    NSApp.mainMenu=menu;
    self.statusItem=[NSStatusBar.systemStatusBar statusItemWithLength:NSVariableStatusItemLength];
    self.statusItem.button.toolTip=@"RobloxStats — tracking in the background";
    self.statusItem.button.image=[NSImage imageWithSystemSymbolName:@"clock" accessibilityDescription:@"RobloxStats"];
    NSMenu *status=[NSMenu new]; [status addItemWithTitle:@"Open dashboard" action:@selector(showWindow:) keyEquivalent:@""];
    [status addItemWithTitle:@"Hide dashboard" action:@selector(hideWindow:) keyEquivalent:@""];
    [status addItemWithTitle:@"Reload dashboard" action:@selector(reload:) keyEquivalent:@""];
    [status addItem:NSMenuItem.separatorItem]; [status addItemWithTitle:@"Quit RobloxStats" action:@selector(terminate:) keyEquivalent:@""]; self.statusItem.menu=status;
    self.web=[WKWebView new]; self.web.navigationDelegate=self;
    self.window=[[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,1180,820) styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable|NSWindowStyleMaskResizable backing:NSBackingStoreBuffered defer:NO];
    self.window.title=@"RobloxStats"; self.window.minSize=NSMakeSize(720,540); self.window.contentView=self.web; self.window.delegate=self; self.window.releasedWhenClosed=NO;
    [self.window setFrameAutosaveName:@"RobloxStatsDashboard"]; [self.window center]; [self showWindow:nil];
    [self.web loadHTMLString:@"<body style='background:#f6f7f1;font:20px -apple-system;padding:60px;color:#294437'><h1>RobloxStats</h1><p>Starting your tracker…</p></body>" baseURL:nil];
    [self probe:0];
}
- (void)showWindow:(id)sender { [self.window makeKeyAndOrderFront:nil]; [NSApp activateIgnoringOtherApps:YES]; }
- (void)hideWindow:(id)sender { [self.window orderOut:nil]; }
- (void)reload:(id)sender { [self.web loadRequest:[NSURLRequest requestWithURL:self.home]]; }
- (BOOL)applicationShouldHandleReopen:(NSApplication *)sender hasVisibleWindows:(BOOL)flag { [self showWindow:nil]; return YES; }
- (BOOL)applicationShouldTerminateAfterLastWindowClosed:(NSApplication *)sender { return NO; }
- (void)fail:(NSString *)message { NSAlert *alert=[NSAlert new]; alert.messageText=@"RobloxStats"; alert.informativeText=message; [alert runModal]; }
- (void)probe:(NSInteger)attempt {
    NSMutableURLRequest *request=[NSMutableURLRequest requestWithURL:[self.home URLByAppendingPathComponent:@"api/dashboard"]]; request.timeoutInterval=1;
    [[NSURLSession.sharedSession dataTaskWithRequest:request completionHandler:^(NSData *data,NSURLResponse *response,NSError *error) {
        id json=data ? [NSJSONSerialization JSONObjectWithData:data options:0 error:nil] : nil;
        dispatch_async(dispatch_get_main_queue(), ^{
            if (self.terminating) return;
            if ([(NSHTTPURLResponse *)response statusCode]==200 && [json isKindOfClass:NSDictionary.class] && json[@"current_stats"] && json[@"token"]) { [self reload:nil]; return; }
            if (response) { [self fail:@"Port 8765 is used by another service. Close that service and reopen RobloxStats."]; return; }
            if (attempt==0 && ![self startTracker]) { [self fail:@"Could not start the tracker. Rebuild the app using scripts/build_macos_app.py."]; return; }
            if (attempt>=30) { [self fail:@"The tracker did not start. Details are in ~/.robloxstats/desktop.log."]; return; }
            dispatch_after(dispatch_time(DISPATCH_TIME_NOW,400*NSEC_PER_MSEC),dispatch_get_main_queue(), ^{ [self probe:attempt+1]; });
        });
    }] resume];
}
- (BOOL)startTracker {
    NSURL *resources=NSBundle.mainBundle.resourceURL;
    NSData *raw=[NSData dataWithContentsOfURL:[resources URLByAppendingPathComponent:@"runtime.json"]];
    NSDictionary *config=raw ? [NSJSONSerialization JSONObjectWithData:raw options:0 error:nil] : nil;
    if (![config[@"python"] isKindOfClass:NSString.class]) return NO;
    NSTask *process=[NSTask new]; process.executableURL=[NSURL fileURLWithPath:config[@"python"]]; process.arguments=@[@"-m",@"robloxstats.app",@"--no-browser"];
    NSMutableDictionary *env=[NSProcessInfo.processInfo.environment mutableCopy]; env[@"PYTHONDONTWRITEBYTECODE"]=@"1"; env[@"PYTHONPATH"]=[resources URLByAppendingPathComponent:@"python"].path;
    NSData *mailRaw=[NSData dataWithContentsOfURL:[dataURL() URLByAppendingPathComponent:@"desktop-email.json"]];
    NSDictionary *mail=mailRaw ? [NSJSONSerialization JSONObjectWithData:mailRaw options:0 error:nil] : nil;
    if ([mail isKindOfClass:NSDictionary.class]) {
        for (NSString *key in @[@"EMAIL_PROVIDER",@"RESEND_FROM"]) if ([mail[key] isKindOfClass:NSString.class]) env[key]=mail[key];
        if (!env[@"RESEND_API_KEY"] && [mail[@"EMAIL_PROVIDER"] isEqual:@"resend"]) {
            NSMutableDictionary *query=keyQuery(); query[(__bridge id)kSecReturnData]=@YES; CFTypeRef result=NULL;
            if (SecItemCopyMatching((__bridge CFDictionaryRef)query,&result)==errSecSuccess) {
                NSData *bytes=CFBridgingRelease(result); NSString *key=[[NSString alloc] initWithData:bytes encoding:NSUTF8StringEncoding]; if (key) env[@"RESEND_API_KEY"]=key;
            }
        }
    }
    process.environment=env;
    [NSFileManager.defaultManager createDirectoryAtURL:dataURL() withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:nil];
    NSString *path=[dataURL() URLByAppendingPathComponent:@"desktop.log"].path;
    if (![NSFileManager.defaultManager fileExistsAtPath:path]) [NSFileManager.defaultManager createFileAtPath:path contents:nil attributes:@{NSFilePosixPermissions:@0600}];
    NSFileHandle *log=[NSFileHandle fileHandleForWritingAtPath:path]; if (!log) return NO; [log seekToEndOfFile];
    process.standardOutput=log; process.standardError=log; process.standardInput=NSFileHandle.fileHandleWithNullDevice;
    __weak AppDelegate *weakSelf=self;
    process.terminationHandler=^(NSTask *task) { dispatch_async(dispatch_get_main_queue(), ^{ AppDelegate *strong=weakSelf; if (strong && !strong.terminating) [strong fail:@"The tracker stopped. Quit and reopen RobloxStats to restart it. See ~/.robloxstats/desktop.log for details."]; }); };
    NSError *error=nil; if (![process launchAndReturnError:&error]) return NO; self.tracker=process; return YES;
}
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)sender {
    self.terminating=YES; if (!self.tracker.running) return NSTerminateNow;
    self.tracker.terminationHandler=^(NSTask *task) { dispatch_async(dispatch_get_main_queue(), ^{ [sender replyToApplicationShouldTerminate:YES]; }); };
    [self.tracker terminate]; return NSTerminateLater;
}
- (void)webView:(WKWebView *)webView decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))decision {
    NSURL *url=action.request.URL;
    if ([url.scheme isEqual:@"about"]) { decision(WKNavigationActionPolicyAllow); return; }
    if ([url.scheme isEqual:@"http"] && [url.host isEqual:@"127.0.0.1"] && url.port.intValue==8765) decision(action.shouldPerformDownload ? WKNavigationActionPolicyDownload : WKNavigationActionPolicyAllow);
    else { if ([@[@"http",@"https"] containsObject:url.scheme]) [NSWorkspace.sharedWorkspace openURL:url]; decision(WKNavigationActionPolicyCancel); }
}
- (void)webView:(WKWebView *)webView decidePolicyForNavigationResponse:(WKNavigationResponse *)response decisionHandler:(void (^)(WKNavigationResponsePolicy))decision { decision([response.response.MIMEType isEqual:@"text/csv"] ? WKNavigationResponsePolicyDownload : WKNavigationResponsePolicyAllow); }
- (void)webView:(WKWebView *)webView navigationAction:(WKNavigationAction *)action didBecomeDownload:(WKDownload *)download { download.delegate=self; }
- (void)webView:(WKWebView *)webView navigationResponse:(WKNavigationResponse *)response didBecomeDownload:(WKDownload *)download { download.delegate=self; }
- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response suggestedFilename:(NSString *)filename completionHandler:(void (^)(NSURL *))completion {
    NSSavePanel *panel=[NSSavePanel savePanel]; panel.nameFieldStringValue=filename;
    [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse result) { completion(result==NSModalResponseOK ? panel.URL : nil); }];
}
@end
int main(int argc,const char *argv[]) {
    @autoreleasepool {
        if (argc>1 && strcmp(argv[1],"--store-email-config")==0) {
            NSData *raw=[NSFileHandle.fileHandleWithStandardInput readDataToEndOfFile];
            id value=[NSJSONSerialization JSONObjectWithData:raw options:NSJSONReadingMutableContainers error:nil];
            if (![value isKindOfClass:NSMutableDictionary.class]) return 1;
            NSMutableDictionary *config=value; NSString *key=config[@"RESEND_API_KEY"];
            if (key) {
                if (![key isKindOfClass:NSString.class]) return 1;
                NSData *bytes=[key dataUsingEncoding:NSUTF8StringEncoding]; NSMutableDictionary *query=keyQuery();
                OSStatus status=SecItemUpdate((__bridge CFDictionaryRef)query,(__bridge CFDictionaryRef)@{(__bridge id)kSecValueData:bytes});
                if (status==errSecItemNotFound) { query[(__bridge id)kSecValueData]=bytes; status=SecItemAdd((__bridge CFDictionaryRef)query,NULL); }
                if (status!=errSecSuccess) return 1;
                [config removeObjectForKey:@"RESEND_API_KEY"];
            }
            [NSFileManager.defaultManager createDirectoryAtURL:dataURL() withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:nil];
            NSURL *path=[dataURL() URLByAppendingPathComponent:@"desktop-email.json"];
            if (![[NSJSONSerialization dataWithJSONObject:config options:0 error:nil] writeToURL:path atomically:YES]) return 1;
            [NSFileManager.defaultManager setAttributes:@{NSFilePosixPermissions:@0600} ofItemAtPath:path.path error:nil]; return 0;
        }
        NSApplication *app=NSApplication.sharedApplication; AppDelegate *delegate=[AppDelegate new]; app.delegate=delegate;
        [app setActivationPolicy:NSApplicationActivationPolicyAccessory]; [app run];
    }
    return 0;
}
