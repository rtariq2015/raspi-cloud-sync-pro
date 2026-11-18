/* Synthetic, hardware-free fixture for the actual patched upstream receiver.
 * GPL-3.0-or-later. It never opens USB/configfs/block devices.
 */
#include "buildconf.h"
#include <stdint.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <unistd.h>
#include "mtp.h"
#include "mtp_constant.h"
#include "mtp_operations.h"
#include "usb_gadget_fct.h"
#include "rcsp_mtp_completion.h"

static const char *scenario, *fixture_root;
static char object_path[4096];
static fs_entry object;
static mtp_ctx context;
static fs_handles_db database;
static unsigned char payload[20000];
static size_t sent, total;
static int object_fd = -1, write_calls, read_calls;

ssize_t __real_write(int fd, const void *data, size_t size);
int __real_fsync(int fd);
int __real_fstatvfs64(int fd, struct statvfs64 *space);
int __wrap_fstatvfs64(int fd, struct statvfs64 *space)
{
    int result=__real_fstatvfs64(fd,space);
    if(!result && fd==object_fd && (!strcmp(scenario,"low-space-start") ||
        (!strcmp(scenario,"low-space-mid") && write_calls>0))) {
        space->f_bavail=1;space->f_frsize=4096;
    }
    return result;
}
ssize_t __wrap_write(int fd, const void *data, size_t size)
{
    if(fd == object_fd) {
        write_calls++;
        if(!strcmp(scenario,"enospc")) {errno=ENOSPC;return -1;}
        if(!strcmp(scenario,"zero-write")) return 0;
        if(!strcmp(scenario,"eintr") && write_calls==1) {errno=EINTR;return -1;}
        if(!strcmp(scenario,"short-write") && write_calls==1 && size>1) return __real_write(fd,data,size/2);
    }
    return __real_write(fd,data,size);
}
int __wrap_fsync(int fd)
{
    if(fd==object_fd && !strcmp(scenario,"fsync-failure")) {errno=EIO;return -1;}
    return __real_fsync(fd);
}
uint32_t peek(void *buffer, int index, int typesize)
{ uint32_t value=0; if(typesize>4) abort();memcpy(&value,(unsigned char*)buffer+index,typesize);return value; }
fs_handles_db *init_fs_db(void *ctx)
{ database.mtp_ctx=ctx;return &database; }
void deinit_fs_db(fs_handles_db *db) { (void)db; }
fs_entry *alloc_root_entry(fs_handles_db *db, uint32_t storage_id)
{ (void)db;(void)storage_id;return &object; }
fs_entry *get_entry_by_handle(fs_handles_db *db, uint32_t handle)
{ (void)db; return handle==7 ? &object : NULL; }
int check_handle_access(mtp_ctx *ctx, fs_entry *entry, uint32_t handle, int write_access, uint32_t *response)
{ (void)ctx;(void)entry;(void)handle;(void)write_access;(void)response;return 0; }
char *mtp_get_storage_root(mtp_ctx *ctx, uint32_t storage_id)
{ (void)ctx;(void)storage_id;return (char*)fixture_root; }
char *build_full_path(fs_handles_db *db, char *root, fs_entry *entry)
{ (void)db;(void)root;(void)entry;return strdup(object_path); }
int entry_open(fs_handles_db *db, fs_entry *entry, int flags, mode_t mode)
{
    (void)db;
    if(!strcmp(scenario,"open-failure")) return -1;
    object_fd = open(object_path,flags,mode);
    entry->file_descriptor = object_fd;
    return object_fd;
}
int read_usb(usb_gadget *usb, unsigned char *buffer, int maxsize)
{
    size_t count;
    (void)usb;read_calls++;
    if(!strcmp(scenario,"disconnect")) {errno=ENODEV;return -1;}
    if(!strcmp(scenario,"cancel")) {context.cancel_req=2;errno=EINTR;return -1;}
    if(!strcmp(scenario,"early-zlp")) return 0;
    if(!strcmp(scenario,"usb-reset-during-read")) rcsp_mtp_invalidate(&context);
    if(!strcmp(scenario,"early-short") && read_calls>1) return 0;
    if(!strcmp(scenario,"path-replaced")) {
        char old[4200];snprintf(old,sizeof(old),"%s.previous",object_path);
        if(rename(object_path,old)) abort();
        int replacement=open(object_path,O_WRONLY|O_CREAT|O_EXCL,0600);if(replacement<0) abort();close(replacement);
    }
    count=total-sent;
    if(count>(size_t)maxsize) count=maxsize;
    if(!strcmp(scenario,"early-short")) count/=2;
    memcpy(buffer,payload+sent,count);sent+=count;
    return (int)count;
}

static void setup(void)
{
    memset(&context,0,sizeof(context));memset(&database,0,sizeof(database));memset(&object,0,sizeof(object));
    pthread_mutex_init(&context.inotify_mutex,NULL);pthread_mutex_init(&context.cancel_mutex,NULL);
    context.session_id=1;context.fs_db=&database;context.usb_rd_buffer_max_size=8192;
    context.rdbuffer2=malloc(8192);if(!context.rdbuffer2) abort();
    object.handle=7;object.storage_id=1;object.file_descriptor=-1;object.name="report.bin";database.mtp_ctx=&context;
}
static uint32_t transfer(size_t bytes, uint32_t transaction)
{
    unsigned char initial[8192];MTP_PACKET_HEADER *header=(MTP_PACKET_HEADER*)initial;
    uint32_t result,params[5]={0};int params_size=0,size;
    size_t first;
    for(size_t n=0;n<bytes;n++) payload[n]=(unsigned char)((n+transaction)%251);
    total=bytes;sent=0;write_calls=0;read_calls=0;
    context.SendObjInfoHandle=7;context.SendObjInfoSize=bytes;context.SendObjInfoOffset=0;
    context.rcsp_info_epoch=rcsp_mtp_epoch(&context);
    if(!strcmp(scenario,"stale-info-epoch")) context.rcsp_info_epoch++;
    if(!strcmp(scenario,"reset-between-info-command")) {
        rcsp_mtp_invalidate(&context);
        /* Simulate old metadata finishing after EP0 invalidated while locked. */
        context.SendObjInfoHandle=7;context.SendObjInfoSize=bytes;context.SendObjInfoOffset=0;
        context.rcsp_info_epoch=rcsp_mtp_epoch(&context)-1;
    }
    if(!strcmp(scenario,"unknown-size")) context.SendObjInfoSize=UINT32_MAX;
    if(!strcmp(scenario,"too-large")) context.SendObjInfoSize=(uint64_t)UINT32_MAX+1;
    if(!strcmp(scenario,"ceiling-metadata")) context.SendObjInfoSize=RCSP_MTP_MAX_OBJECT_SIZE;
    if(!strcmp(scenario,"above-ceiling")) context.SendObjInfoSize=RCSP_MTP_MAX_OBJECT_SIZE+1;
    memset(initial,0,sizeof(initial));header->length=sizeof(*header);header->operation=MTP_CONTAINER_TYPE_COMMAND;
    header->code=!strcmp(scenario,"partial-edit")?MTP_OPERATION_SEND_PARTIAL_OBJECT:MTP_OPERATION_SEND_OBJECT;
    header->tx_id=transaction;size=sizeof(*header);
    result=mtp_op_SendObject(&context,header,&size,params,&params_size);
    if(!strcmp(scenario,"unknown-size") || !strcmp(scenario,"too-large") || !strcmp(scenario,"above-ceiling") || !strcmp(scenario,"partial-edit") || !strcmp(scenario,"stale-info-epoch") || !strcmp(scenario,"reset-between-info-command")) return result;
    if(result!=MTP_RESPONSE_NO_RESPONSE) return result;
    if(!strcmp(scenario,"ceiling-metadata")) return result;
    if(!strcmp(scenario,"session-replay")) context.session_id++;
    if(!strcmp(scenario,"session-closed")) context.session_id=0;
    if(!strcmp(scenario,"usb-reset-before-data")) rcsp_mtp_invalidate(&context);
    if(!strcmp(scenario,"close-open-same-session")) {
        unsigned char session_packet[16]={0};MTP_PACKET_HEADER *session_header=(MTP_PACKET_HEADER*)session_packet;
        int session_size=12;session_header->operation=MTP_CONTAINER_TYPE_COMMAND;session_header->length=12;
        session_header->code=MTP_OPERATION_CLOSE_SESSION;
        if(mtp_op_CloseSession(&context,session_header,&session_size,params,&params_size)!=MTP_RESPONSE_OK) abort();
        session_header->code=MTP_OPERATION_OPEN_SESSION;session_header->length=16;session_size=16;
        uint32_t session_id=1;memcpy(session_packet+12,&session_id,4);
        if(mtp_op_OpenSession(&context,session_header,&session_size,params,&params_size)!=MTP_RESPONSE_OK) abort();
    }
    if(!strcmp(scenario,"malformed-command-replay") || !strcmp(scenario,"unsupported-command-replay")) {
        MTP_PACKET_HEADER bad=*header;int badsize=sizeof(bad);
        if(!strcmp(scenario,"malformed-command-replay")) bad.length++;
        else bad.code=MTP_OPERATION_SEND_PARTIAL_OBJECT;
        if(mtp_op_SendObject(&context,&bad,&badsize,params,&params_size)==MTP_RESPONSE_OK) abort();
    }
    first=bytes;if(first>sizeof(initial)-sizeof(*header)) first=sizeof(initial)-sizeof(*header);
    memcpy(initial+sizeof(*header),payload,first);sent=first;
    header->length=(uint32_t)(bytes+sizeof(*header));header->operation=MTP_CONTAINER_TYPE_DATA;size=(int)(first+sizeof(*header));
    if(!strcmp(scenario,"wrong-transaction")) header->tx_id++;
    if(!strcmp(scenario,"wrong-length")) header->length++;
    if(!strcmp(scenario,"unarmed")) context.rcsp_send_armed=0;
    if(!strcmp(scenario,"cancel-before-data")) context.cancel_req=2;
    if(!strcmp(scenario,"oversized-first-packet")) {size++;}
    return mtp_op_SendObject(&context,header,&size,params,&params_size);
}
int main(int argc,char **argv)
{
    uint32_t result;int expect_ok;
    if(argc!=3) return 64;
    fixture_root=argv[1];scenario=argv[2];
    if(!realpath(fixture_root,NULL) || fixture_root[0]!='/') return 65;
    if(snprintf(object_path,sizeof(object_path),"%s/report.bin",fixture_root)>=(int)sizeof(object_path)) return 66;
    setup();
    if(!strcmp(scenario,"symlink")) {
        char target[4096];snprintf(target,sizeof(target),"%s/target.bin",fixture_root);
        int fd=open(target,O_WRONLY|O_CREAT|O_EXCL,0600);if(fd<0) return 67;close(fd);
        if(symlink(target,object_path)) return 68;
    }
    if(strcmp(scenario,"symlink")) {
        /* SendObjectInfo creates the export-view file before SendObject. */
        int created=open(object_path,O_WRONLY|O_CREAT|O_EXCL,0600);
        if(created<0) return 69;
        close(created);
    }
    result=transfer(!strcmp(scenario,"zero-file")?0:10000,900);
    expect_ok=!strcmp(scenario,"complete")||!strcmp(scenario,"zero-file")||!strcmp(scenario,"short-write")||!strcmp(scenario,"eintr")||!strcmp(scenario,"consecutive");
    if(!strcmp(scenario,"ceiling-metadata")) {
        if(result!=MTP_RESPONSE_NO_RESPONSE) return 10;
    } else if(expect_ok) {
        if(result!=MTP_RESPONSE_OK) {fprintf(stderr,"Expected OK, received0x%x\n",result);return 11;}
    } else {
        if(result==MTP_RESPONSE_OK || (result==MTP_RESPONSE_NO_RESPONSE && strcmp(scenario,"cancel") && strcmp(scenario,"cancel-before-data"))) {
            fprintf(stderr,"Unexpected success0x%x\n",result);return 12;
        }
    }
    if(!strcmp(scenario,"consecutive")) {
        result=transfer(12000,901);if(result!=MTP_RESPONSE_OK) return 13;
        result=transfer(13000,902);if(result!=MTP_RESPONSE_OK) return 14;
    }
    printf("{\"scenario\":\"%s\",\"status\":\"PASS\",\"response\":%u,\"read_calls\":%d,\"write_calls\":%d}\n",scenario,result,read_calls,write_calls);
    free(context.rdbuffer2);pthread_mutex_destroy(&context.inotify_mutex);pthread_mutex_destroy(&context.cancel_mutex);
    return 0;
}
