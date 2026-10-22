# include <bits/stdc++.h>

using namespace std;

int main() {
    int tc;
    cin>>tc;
    while (tc--)
    {
        /* code */
        int n,p;

        cin>>n>>p;
        vector<int> a(n);
        vector<int> b(n);

        for(int i=0;i<n;i++){
            cin>>a[i];
        }

        for(int i=0;i<n;i++){
            cin>>b[i];
        }

        vector<pair<int,int>> v(n);

        for(int i=0;i<n;i++){
            v[i]={b[i],a[i]};
        }

        sort(v.begin(),v.end());


        int min=v[0].first;
        int cost=0;
        cost+=p;
        int n1=n;
        n1--;
        int i=0;
        while(n1>0 && p>min){
            

            if(n1-v[i].second<=0){
                cost+=n1*min;
                n1=0;
            }else{
                n1-=v[i].second;
                cost+=v[i].second*min;
            }
            i++;
            min=v[i].first;


        }
        

        if(n1!=0){
            cost+=n1*p;
        }

        cout<<cost<<endl;




    
    }

    return 0;
    
}